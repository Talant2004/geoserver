"""Окно «Найти очаги»: превью с ползунком чувствительности и отправка кандидатов в приложение."""
from __future__ import annotations

import base64
import queue
import struct
import threading
import zlib
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import numpy as np

import povilika_cli as cli
import povilika_detect as det

CANVAS_MAX = 900


def _photo(rgb: np.ndarray) -> tk.PhotoImage:
    h, w, _ = rgb.shape
    rows = np.concatenate([np.zeros((h, 1), np.uint8), np.ascontiguousarray(rgb).reshape(h, w * 3)], axis=1)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows.tobytes(), 1)) + chunk(b"IEND", b""))
    return tk.PhotoImage(data=base64.b64encode(png), format="PNG")


class DetectWindow(tk.Toplevel):
    def __init__(self, master, cfg: cli.Config, survey: dict):
        super().__init__(master, class_="Povilika")
        self.cfg = cfg
        self.survey = survey
        self.title(f"Поиск очагов — {survey['farm']} / {survey['field']} / {survey['survey_date'][:10]}")
        self.path = str(survey["file_path"]).replace(cfg.container_dir, str(cfg.host_dir), 1)
        self.candidates: list[det.Candidate] = []
        self._pending = None

        self.sens = tk.IntVar(value=40)
        self.res = tk.DoubleVar(value=0.5)
        self.min_area = tk.DoubleVar(value=12.5)
        self.buffer = tk.DoubleVar(value=2.0)
        self.fill = tk.BooleanVar(value=True)
        self.result = tk.StringVar(value="Подберите чувствительность и нажмите «Рассчитать очаги».")

        if not Path(self.path).exists():
            messagebox.showerror("Файл не найден", self.path, parent=master)
            self.destroy()
            return
        self.preview = det.load_preview(self.path, CANVAS_MAX)
        h, w = self.preview.rgb.shape[:2]
        self.edits = np.zeros((h, w), np.uint8)
        self._base_mask = None
        self._base_sens = None
        self._last_xy = None
        self.brush = tk.IntVar(value=15)

        self.canvas = tk.Canvas(self, width=w, height=h, bg="#222", highlightthickness=0, cursor="none")
        self.canvas.grid(row=0, column=0, padx=8, pady=8, sticky="n")
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.cursor_item = self.canvas.create_oval(0, 0, 0, 0, outline="white", width=1, state="hidden")
        for button, mode in (("1", 1), ("3", 2)):
            self.canvas.bind(f"<ButtonPress-{button}>", lambda e, m=mode: self._paint(e, m, start=True))
            self.canvas.bind(f"<B{button}-Motion>", lambda e, m=mode: self._paint(e, m))
            self.canvas.bind(f"<ButtonRelease-{button}>", lambda _e: setattr(self, "_last_xy", None))
        self.canvas.bind("<Motion>", self._move_cursor)
        self.canvas.bind("<Leave>", lambda _e: self.canvas.itemconfigure(self.cursor_item, state="hidden"))

        panel = ttk.Frame(self, padding=8)
        panel.grid(row=0, column=1, sticky="ns")
        ttk.Label(panel, text="Чувствительность").pack(anchor="w")
        ttk.Scale(panel, from_=0, to=100, variable=self.sens, length=260,
                  command=lambda _v: self._schedule_mask()).pack(anchor="w")
        self.sens_label = ttk.Label(panel, text="40")
        self.sens_label.pack(anchor="w", pady=(0, 10))
        ttk.Label(panel, text="Красным — пиксели, похожие на повилику по цвету.",
                  foreground="#666", wraplength=260).pack(anchor="w", pady=(0, 12))

        ttk.Label(panel, text="Кисть: левая кнопка — добавить, правая — стереть",
                  wraplength=260).pack(anchor="w")
        brush_row = ttk.Frame(panel)
        brush_row.pack(anchor="w", fill="x")
        ttk.Label(brush_row, text="Размер").pack(side="left")
        ttk.Scale(brush_row, from_=2, to=80, variable=self.brush, length=150,
                  command=lambda _v: self.brush_label.configure(text=f"{self.brush.get()} пикс.")).pack(side="left", padx=6)
        self.brush_label = ttk.Label(brush_row, text="15 пикс.", width=8)
        self.brush_label.pack(side="left")
        ttk.Button(panel, text="Сбросить ручные правки", command=self._clear_edits).pack(anchor="w", pady=(4, 12))

        form = ttk.Frame(panel)
        form.pack(anchor="w", fill="x")
        for i, (label, var, lo, hi, step) in enumerate((
            ("Разрешение расчёта, м", self.res, 0.1, 5, 0.1),
            ("Мин. площадь очага, м²", self.min_area, 1, 1000, 0.5),
            ("Буфер вокруг очага, м", self.buffer, 0, 50, 0.5),
        )):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", pady=3)
            ttk.Spinbox(form, textvariable=var, from_=lo, to=hi, increment=step, width=8,
                        command=self._invalidate).grid(row=i, column=1, padx=6)
        ttk.Checkbutton(panel, text="Заливать дыры внутри очагов", variable=self.fill,
                        command=self._invalidate).pack(anchor="w", pady=8)

        self.calc_btn = ttk.Button(panel, text="Рассчитать очаги", command=self.calculate)
        self.calc_btn.pack(fill="x", pady=(8, 4))
        ttk.Label(panel, textvariable=self.result, wraplength=260).pack(anchor="w", pady=6)
        self.send_btn = ttk.Button(panel, text="Отправить в приложение", command=self.send, state="disabled")
        self.send_btn.pack(fill="x")
        ttk.Label(panel, text="Прежние автоматические кандидаты этого снимка будут заменены. "
                              "Очаги, нарисованные в приложении вручную, не затрагиваются.",
                  foreground="#666", wraplength=260).pack(anchor="w", pady=(8, 0))

        self.transient(master)
        self._redraw_mask()

    def _schedule_mask(self) -> None:
        self.sens_label.configure(text=str(self.sens.get()))
        self._invalidate()
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(120, self._redraw_mask)

    def _move_cursor(self, e) -> None:
        r = self.brush.get()
        self.canvas.coords(self.cursor_item, e.x - r, e.y - r, e.x + r, e.y + r)
        self.canvas.itemconfigure(self.cursor_item, state="normal")
        self.canvas.tag_raise(self.cursor_item)

    def _stamp(self, x: float, y: float, r: int, mode: int) -> None:
        h, w = self.edits.shape
        x0, x1 = max(0, int(x - r)), min(w, int(x + r) + 1)
        y0, y1 = max(0, int(y - r)), min(h, int(y + r) + 1)
        if x0 >= x1 or y0 >= y1:
            return
        yy, xx = np.ogrid[y0:y1, x0:x1]
        self.edits[y0:y1, x0:x1][(xx - x) ** 2 + (yy - y) ** 2 <= r * r] = mode

    def _paint(self, e, mode: int, start: bool = False) -> None:
        self._move_cursor(e)
        r = self.brush.get()
        x0, y0 = (e.x, e.y) if start or self._last_xy is None else self._last_xy
        steps = max(1, int(np.hypot(e.x - x0, e.y - y0) / max(1, r / 2)))
        for t in np.linspace(0, 1, steps + 1):
            self._stamp(x0 + (e.x - x0) * t, y0 + (e.y - y0) * t, r, mode)
        self._last_xy = (e.x, e.y)
        self._invalidate()
        if not self._pending:
            self._pending = self.after(40, self._redraw_mask)

    def _clear_edits(self) -> None:
        self.edits[:] = 0
        self._invalidate()
        self._redraw_mask()

    def _redraw_mask(self) -> None:
        self._pending = None
        p = self.preview
        if self._base_sens != self.sens.get():
            self._base_mask = det.color_mask(p.rgb, p.valid, self.sens.get())
            self._base_sens = self.sens.get()
        mask = det.apply_edits(self._base_mask, p.valid, self.edits)
        img = p.rgb.copy()
        img[~p.valid] = (34, 34, 34)
        img[mask] = (255, 0, 0)
        self._img = _photo(img)
        self.canvas.itemconfigure(self.image_item, image=self._img)

    def _invalidate(self) -> None:
        if self.candidates:
            self.candidates = []
            self.canvas.delete("outline")
            self.send_btn.configure(state="disabled")
            self.result.set("Параметры изменились — пересчитайте очаги.")

    def calculate(self) -> None:
        self.calc_btn.configure(state="disabled")
        self.result.set("Идёт расчёт…")
        params = dict(resolution_m=float(self.res.get()), min_area_m2=float(self.min_area.get()),
                      buffer_m=float(self.buffer.get()), fill_holes=self.fill.get(), edits=self.edits.copy())
        sens = self.sens.get()
        result: queue.Queue = queue.Queue()

        def run() -> None:
            try:
                result.put((det.detect(self.path, sens, **params), None))
            except Exception as e:  # noqa: BLE001
                result.put(([], e))

        def poll() -> None:
            try:
                self._show(*result.get_nowait())
            except queue.Empty:
                self.after(100, poll)

        threading.Thread(target=run, daemon=True).start()
        poll()

    def _show(self, found: list[det.Candidate], error) -> None:
        self.calc_btn.configure(state="normal")
        self.canvas.delete("outline")
        if error:
            self.result.set(f"Ошибка: {error}")
            return
        self.candidates = found
        for c in found:
            pts = det.to_preview_pixels(c.polygon, self.preview)
            self.canvas.create_polygon(*[v for xy in pts for v in xy], outline="#ffeb3b", fill="",
                                       width=2, tags="outline")
        total = sum(c.area_ha for c in found)
        self.result.set(f"Найдено очагов: {len(found)}, площадь {total:.3f} га" if found
                        else "Очагов не найдено. Попробуйте увеличить чувствительность.")
        self.send_btn.configure(state="normal" if found else "disabled")

    def send(self) -> None:
        if not self.candidates:
            return
        n, total = len(self.candidates), sum(c.area_ha for c in self.candidates)
        if not messagebox.askyesno("Отправить в приложение",
                                   f"Отправить {n} кандидатов ({total:.3f} га) в приложение?", parent=self):
            return
        try:
            sent = cli.publish_candidates(self.cfg, self.survey,
                                          [(c.polygon, c.area_ha) for c in self.candidates], self.sens.get())
        except (SystemExit, Exception) as e:  # noqa: BLE001
            messagebox.showerror("Ошибка", str(e), parent=self)
            return
        messagebox.showinfo("Готово", f"Отправлено очагов: {sent}.\n"
                                      "В приложении они показаны оранжевым как «авто».", parent=self)

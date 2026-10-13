#!/usr/bin/env python3
"""Повилика: окно загрузки снимков в архив GeoServer."""
from __future__ import annotations

import datetime as dt
import os
import queue
import re
import subprocess
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urlencode

import povilika_cli as cli

SUPPORTED = (".tif", ".tiff", ".gpkg", ".shp")
NAME_RE = re.compile(r"^(\d{2})(\d{2})(\d{4})_(.+?)(?:_(RGB|MS|NDVI|DSM|DTM|ortho))?$", re.I)
GENERIC_DIRS = {"processed", "ortho", "orthophoto", "result", "results", "output", "export"}


def guess_from_name(path: Path) -> tuple[str, str]:
    """'19082026_Untymak_RGB' -> ('Untymak', '2026-08-19')."""
    m = NAME_RE.match(path.stem)
    if not m:
        return path.stem, ""
    d, mo, y, field, _ = m.groups()
    try:
        return field, dt.date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return field, ""


def guess_farm(folder: Path) -> str:
    name = folder.name
    if name.lower() in GENERIC_DIRS and folder.parent.name:
        name = folder.parent.name
    return name[:1].upper() + name[1:]


def human_size(n: int) -> str:
    return f"{n / 2**30:.2f} ГБ" if n >= 2**30 else f"{n / 2**20:.0f} МБ"


class App(tk.Tk):
    def __init__(self) -> None:
        # className должен совпадать с StartupWMClass в .desktop, иначе окно не попадёт на значок в Dock.
        super().__init__(className="Povilika")
        self.title("Повилика — загрузка снимков")
        icon = Path(__file__).with_name("assets") / "povilika_128.png"
        if icon.exists():
            self._icon = tk.PhotoImage(file=str(icon))
            self.iconphoto(True, self._icon)
        self.geometry("980x620")
        self.minsize(820, 500)
        self.cfg = cli.Config.from_env(cli.DEFAULT_ENV)
        self.rows: dict[str, dict] = {}
        self.events: queue.Queue = queue.Queue()
        self.busy = False

        self.farm = tk.StringVar()
        self.move = tk.BooleanVar(value=False)
        self.replace = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="")

        self.banner = ttk.Frame(self, padding=(10, 6))
        self.banner_text = ttk.Label(self.banner, foreground="#b71c1c")
        self.banner_text.pack(side="left")
        ttk.Button(self.banner, text="Запустить сервер", command=self.start_server).pack(side="right")

        nb = self.nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)
        nb.add(self._upload_tab(nb), text="  Загрузка  ")
        nb.add(self._published_tab(nb), text="  Опубликованные  ")
        nb.bind("<<NotebookTabChanged>>", lambda e: nb.index("current") == 1 and self.refresh_published())

        self.after(100, self._poll)
        self.after(200, self.check_server)

    # ------------------------------------------------------------ upload tab

    def _upload_tab(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=8)

        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Button(top, text="Выбрать папку…", command=self.pick_folder).pack(side="left")
        ttk.Button(top, text="Добавить файлы…", command=self.pick_files).pack(side="left", padx=6)
        ttk.Button(top, text="Убрать выбранные", command=self.remove_selected).pack(side="left")

        opts = ttk.Frame(f)
        opts.pack(fill="x", pady=8)
        ttk.Label(opts, text="Хозяйство / район:").pack(side="left")
        ttk.Entry(opts, textvariable=self.farm, width=28).pack(side="left", padx=6)
        ttk.Checkbutton(opts, text="Перезаписывать уже загруженные", variable=self.replace).pack(side="left", padx=12)
        ttk.Checkbutton(opts, text="Удалять с исходного диска", variable=self.move).pack(side="left")

        cols = ("file", "size", "field", "date", "status")
        self.table = ttk.Treeview(f, columns=cols, show="headings", selectmode="extended", height=10)
        for col, title, width, anchor in (
            ("file", "Файл", 330, "w"), ("size", "Размер", 80, "e"), ("field", "Поле / участок", 200, "w"),
            ("date", "Дата съёмки", 100, "center"), ("status", "Статус", 200, "w"),
        ):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, anchor=anchor)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<Double-1>", self.edit_row)
        ttk.Label(f, text="Двойной щелчок по строке — изменить поле и дату.", foreground="#666").pack(anchor="w")

        bottom = ttk.Frame(f)
        bottom.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(bottom, mode="determinate", maximum=1.0)
        self.progress.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status).pack(anchor="w", pady=4)
        buttons = ttk.Frame(bottom)
        buttons.pack(anchor="e")
        push_icon = Path(__file__).with_name("assets") / "push_28.png"
        if push_icon.exists():
            self._push_icon = tk.PhotoImage(file=str(push_icon))
            self.push_btn = ttk.Button(buttons, image=self._push_icon, command=self.git_push)
        else:
            self.push_btn = ttk.Button(buttons, text="⇪", width=3, command=self.git_push)
        self.push_btn.pack(side="left", padx=(0, 6))
        self.start_btn = ttk.Button(buttons, text="Загрузить", command=self.start_upload)
        self.start_btn.pack(side="left", ipady=4)

        self.log = tk.Text(f, height=6, state="disabled", font=("Monospace", 9))
        self.log.pack(fill="x", pady=(8, 0))
        return f

    def pick_folder(self) -> None:
        folder = filedialog.askdirectory(title="Папка со снимками", initialdir="/media")
        if not folder:
            return
        folder = Path(folder)
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in SUPPORTED)
        if not files:
            messagebox.showinfo("Нет файлов", "В папке нет файлов .tif / .gpkg / .shp")
            return
        if not self.farm.get():
            self.farm.set(guess_farm(folder))
        self.add_files(files)

    def pick_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Снимки", initialdir="/media",
            filetypes=[("Геоданные", "*.tif *.tiff *.TIF *.gpkg *.shp"), ("Все файлы", "*")],
        )
        if paths:
            if not self.farm.get():
                self.farm.set(guess_farm(Path(paths[0]).parent))
            self.add_files([Path(p) for p in paths])

    def add_files(self, files: list[Path]) -> None:
        known = {r["path"] for r in self.rows.values()}
        for p in files:
            if p in known:
                continue
            field, date = guess_from_name(p)
            iid = self.table.insert("", "end", values=(p.name, human_size(p.stat().st_size), field, date, "ожидает"))
            self.rows[iid] = {"path": p, "field": field, "date": date, "done": False}

    def remove_selected(self) -> None:
        if self.busy:
            return
        for iid in self.table.selection():
            self.table.delete(iid)
            self.rows.pop(iid, None)

    def edit_row(self, event) -> None:
        iid = self.table.identify_row(event.y)
        if not iid or self.busy:
            return
        row = self.rows[iid]
        dlg = tk.Toplevel(self, class_="Povilika")
        dlg.title(row["path"].name)
        dlg.transient(self)
        dlg.resizable(False, False)
        field, date = tk.StringVar(value=row["field"]), tk.StringVar(value=row["date"])
        ttk.Label(dlg, text="Поле / участок:").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        ttk.Entry(dlg, textvariable=field, width=32).grid(row=0, column=1, padx=10, pady=(10, 4))
        ttk.Label(dlg, text="Дата (ГГГГ-ММ-ДД):").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        ttk.Entry(dlg, textvariable=date, width=32).grid(row=1, column=1, padx=10, pady=4)

        def save() -> None:
            try:
                dt.date.fromisoformat(date.get().strip())
            except ValueError:
                messagebox.showerror("Дата", "Дата должна быть в формате ГГГГ-ММ-ДД", parent=dlg)
                return
            row["field"], row["date"] = field.get().strip(), date.get().strip()
            self.table.set(iid, "field", row["field"])
            self.table.set(iid, "date", row["date"])
            dlg.destroy()

        ttk.Button(dlg, text="Сохранить", command=save).grid(row=2, column=1, sticky="e", padx=10, pady=10)
        dlg.grab_set()

    def write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start_upload(self) -> None:
        todo = [(iid, r) for iid, r in self.rows.items() if not r["done"]]
        if not todo:
            messagebox.showinfo("Нечего загружать", "Добавьте файлы кнопкой «Выбрать папку…»")
            return
        farm = self.farm.get().strip()
        if not farm:
            messagebox.showerror("Хозяйство", "Укажите хозяйство или район")
            return
        bad = [r["path"].name for _, r in todo if not r["field"] or not _valid_date(r["date"])]
        if bad:
            messagebox.showerror("Не хватает данных",
                                 "Укажите поле и дату (двойной щелчок по строке):\n\n" + "\n".join(bad))
            return
        if self.move.get() and not messagebox.askyesno(
                "Удаление исходников", "После загрузки файлы будут удалены с исходного диска. Продолжить?"):
            return
        self.busy = True
        self.start_btn.configure(state="disabled")
        args = (todo, farm, self.move.get(), self.replace.get())
        threading.Thread(target=self._upload_worker, args=args, daemon=True).start()

    def _upload_worker(self, todo, farm, move, replace) -> None:
        total = sum(r["path"].stat().st_size for _, r in todo) or 1
        done_bytes = 0
        ok = 0
        for iid, r in todo:
            size = r["path"].stat().st_size
            name = r["path"].name

            def progress(done, _total, base=done_bytes, iid=iid, name=name):
                pct = done / _total * 100
                self.events.put(("status", iid, f"копирование {pct:.0f}%"))
                self.events.put(("progress", (base + done) / total, f"{name}: копирование {pct:.0f}%"))

            def log(text, iid=iid, name=name):
                if text.startswith("[+] файл"):
                    self.events.put(("status", iid, "публикация…"))
                    self.events.put(("progress", None, f"{name}: публикация в GeoServer…"))
                self.events.put(("log", text))

            self.events.put(("status", iid, "начинаю…"))
            try:
                layers = cli.ingest_file(self.cfg, r["path"], farm=farm, field=r["field"], date=r["date"],
                                         move=move, replace=replace, progress=progress, log=log)
                r["done"] = True
                ok += 1
                self.events.put(("status", iid, "✔ опубликован"))
                self.events.put(("log", f"✔ {name} → {', '.join(layers)}"))
            except SystemExit as e:
                self.events.put(("status", iid, "✖ ошибка"))
                self.events.put(("log", f"✖ {name}: {e}"))
            except Exception as e:  # noqa: BLE001 — показать любую ошибку пользователю
                self.events.put(("status", iid, "✖ ошибка"))
                self.events.put(("log", f"✖ {name}: {type(e).__name__}: {e}"))
            done_bytes += size
        self.events.put(("finished", ok, len(todo)))

    # ------------------------------------------------------------ published tab

    def _published_tab(self, parent) -> ttk.Frame:
        f = ttk.Frame(parent, padding=8)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="Обновить", command=self.refresh_published).pack(side="left")
        ttk.Button(bar, text="Посмотреть на карте", command=self.preview).pack(side="left", padx=6)
        ttk.Button(bar, text="Найти очаги…", command=self.find_foci).pack(side="left")
        ttk.Button(bar, text="Снять с публикации", command=self.unpublish).pack(side="left", padx=6)

        cols = ("date", "farm", "field", "kind", "layer")
        self.pub = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for col, title, width in (("date", "Дата", 100), ("farm", "Хозяйство", 160), ("field", "Поле", 200),
                                  ("kind", "Тип", 70), ("layer", "Слой", 380)):
            self.pub.heading(col, text=title)
            self.pub.column(col, width=width)
        self.pub.pack(fill="both", expand=True)
        self.pub.bind("<Double-1>", lambda e: self.preview())
        self.pub_features: dict[str, dict] = {}
        return f

    def refresh_published(self) -> None:
        try:
            feats = cli.GeoServer(self.cfg).wfs_features("surveys")
        except (SystemExit, Exception) as e:  # noqa: BLE001
            messagebox.showerror("Сервер", f"Не удалось получить список: {e}")
            return
        self.pub.delete(*self.pub.get_children())
        self.pub_features.clear()
        feats.sort(key=lambda x: x["properties"]["survey_date"], reverse=True)
        for feat in feats:
            p = feat["properties"]
            kind = "растр" if p["kind"] == "raster" else "вектор"
            iid = self.pub.insert("", "end", values=(p["survey_date"][:10], p["farm"], p["field"], kind, p["layer_name"]))
            self.pub_features[iid] = feat

    def _selected_feature(self) -> dict | None:
        sel = self.pub.selection()
        if not sel:
            messagebox.showinfo("Слой", "Выберите строку в списке")
            return None
        return self.pub_features[sel[0]]

    def preview(self) -> None:
        feat = self._selected_feature()
        if not feat:
            return
        ring = feat["geometry"]["coordinates"][0]
        xs, ys = [c[0] for c in ring], [c[1] for c in ring]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        width = 1024
        height = max(256, min(1024, int(width * (maxy - miny) / ((maxx - minx) or 1))))
        query = urlencode({
            "service": "WMS", "version": "1.1.0", "request": "GetMap",
            "layers": f"{cli.WORKSPACE}:{feat['properties']['layer_name']}", "styles": "",
            "bbox": f"{minx},{miny},{maxx},{maxy}", "width": width, "height": height,
            "srs": "EPSG:4326", "format": "application/openlayers",
        })
        webbrowser.open(f"{self.cfg.url}/{cli.WORKSPACE}/wms?{query}")

    def find_foci(self) -> None:
        feat = self._selected_feature()
        if not feat:
            return
        if feat["properties"]["kind"] != "raster":
            messagebox.showinfo("Поиск очагов", "Поиск работает только по растровым снимкам (RGB).")
            return
        try:
            from povilika_detect_gui import DetectWindow
        except ImportError as e:
            messagebox.showerror("Поиск очагов", f"Не установлены библиотеки для растров: {e}\n"
                                 "Запускайте программу через ярлык на рабочем столе.")
            return
        DetectWindow(self, self.cfg, feat["properties"])

    def unpublish(self) -> None:
        feat = self._selected_feature()
        if not feat:
            return
        layer = feat["properties"]["layer_name"]
        if not messagebox.askyesno("Снять с публикации",
                                   f"Убрать слой {layer} из GeoServer и приложения?\n\n"
                                   "Файл в архиве на диске останется."):
            return
        try:
            gs = cli.GeoServer(self.cfg)
            cli._remove_store(gs, Path(feat["properties"]["file_path"]).stem)
        except (SystemExit, Exception) as e:  # noqa: BLE001
            messagebox.showerror("Ошибка", str(e))
        self.refresh_published()

    # ------------------------------------------------------------ server

    def check_server(self) -> None:
        try:
            ok = cli.GeoServer(self.cfg).get("/about/version").status_code == 200
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            self.banner.pack_forget()
        else:
            self.banner_text.configure(text="Сервер GeoServer не отвечает.")
            self.banner.pack(fill="x", before=self.nb)

    def start_server(self) -> None:
        self.banner_text.configure(text="Запускаю сервер… (до 2 минут)")
        compose_dir = cli.ROOT / "server"

        def run() -> None:
            subprocess.run(["docker", "compose", "up", "-d"], cwd=compose_dir, capture_output=True)
            for _ in range(40):
                try:
                    if cli.GeoServer(self.cfg).get("/about/version").status_code == 200:
                        break
                except Exception:  # noqa: BLE001
                    pass
                threading.Event().wait(3)
            self.events.put(("check",))

        threading.Thread(target=run, daemon=True).start()

    def git_push(self) -> None:
        """Коммитит изменения проекта и отправляет их на GitHub (ключ и имя из .git/config)."""
        self.push_btn.configure(state="disabled")
        repo = cli.ROOT

        # Без CURSOR_*: при CURSOR_AGENT git дописывает в коммит соавтора Cursor.
        env = {k: v for k, v in os.environ.items() if not k.startswith("CURSOR")}
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=20"

        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=120, env=env)

        def run() -> None:
            try:
                if git("status", "--porcelain").stdout.strip():
                    git("add", "-A")
                    r = git("commit", "-q", "-m", f"Обновление {dt.datetime.now():%Y-%m-%d %H:%M}")
                    if r.returncode:
                        raise RuntimeError(r.stderr or r.stdout)
                r = git("pull", "-q", "--rebase", "origin", "main")
                if r.returncode:
                    git("rebase", "--abort")
                    raise RuntimeError(r.stderr or r.stdout)
                r = git("push", "-q", "origin", "HEAD:main")
                if r.returncode:
                    raise RuntimeError(r.stderr or r.stdout)
                head = git("log", "-1", "--format=%h %s").stdout.strip()
                self.events.put(("pushed", True, head))
            except Exception as e:  # noqa: BLE001
                self.events.put(("pushed", False, str(e).strip()))

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ events

    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "status":
                    if self.table.exists(ev[1]):
                        self.table.set(ev[1], "status", ev[2])
                elif kind == "progress":
                    if ev[1] is not None:
                        self.progress["value"] = ev[1]
                    self.status.set(ev[2])
                elif kind == "log":
                    self.write_log(ev[1])
                elif kind == "finished":
                    self.busy = False
                    self.start_btn.configure(state="normal")
                    self.progress["value"] = 1.0
                    self.status.set(f"Готово: {ev[1]} из {ev[2]}")
                    messagebox.showinfo("Загрузка завершена", f"Опубликовано {ev[1]} из {ev[2]} файлов.")
                elif kind == "check":
                    self.check_server()
                elif kind == "pushed":
                    self.push_btn.configure(state="normal")
                    if ev[1]:
                        self.write_log(f"✔ GitHub: {ev[2]}")
                    else:
                        self.write_log(f"✖ GitHub: {ev[2]}")
                        messagebox.showerror("GitHub", ev[2])
        except queue.Empty:
            pass
        self.after(100, self._poll)


def _valid_date(s: str) -> bool:
    try:
        dt.date.fromisoformat(s)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    App().mainloop()

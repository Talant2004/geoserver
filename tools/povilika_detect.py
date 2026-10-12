"""Поиск кандидатов в очаги повилики на RGB-ортофотоплане (по цвету в HSV).

Логика порогов перенесена из CuscutaDetector.py. Отличия: очаги остаются отдельными
полигонами (а не одним мультиполигоном), буфер и площадь считаются в метрах зоны UTM.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import shapes, sieve
from rasterio.warp import transform_geom
from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union


@dataclass
class Preview:
    rgb: np.ndarray          # (h, w, 3) uint8
    valid: np.ndarray        # (h, w) bool — пиксели внутри снимка
    transform: rasterio.Affine
    crs: rasterio.crs.CRS


@dataclass
class Candidate:
    polygon: Polygon         # EPSG:4326
    area_ha: float


def _read_rgb(src, out_shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    rgb = src.read((1, 2, 3), out_shape=(3, *out_shape), resampling=Resampling.bilinear)
    valid = src.read_masks(1, out_shape=out_shape, resampling=Resampling.nearest) > 0
    rgb = np.transpose(rgb, (1, 2, 0))
    if rgb.dtype != np.uint8:
        lo, hi = np.percentile(rgb[valid], (0.5, 99.5)) if valid.any() else (rgb.min(), rgb.max())
        rgb = np.clip((rgb.astype(np.float32) - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
    black = (rgb == 0).all(axis=2)
    white = (rgb == 255).all(axis=2)
    return rgb, valid & ~black & ~white


def load_preview(path: str, max_side: int = 1000) -> Preview:
    with rasterio.open(path) as src:
        scale = min(1.0, max_side / max(src.width, src.height))
        out_shape = (max(1, int(src.height * scale)), max(1, int(src.width * scale)))
        rgb, valid = _read_rgb(src, out_shape)
        transform = src.transform * src.transform.scale(src.width / out_shape[1], src.height / out_shape[0])
        return Preview(rgb, valid, transform, src.crs)


def _rgb_to_hsv_cv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """HSV в шкалах OpenCV для uint8: H 0..180, S и V 0..255."""
    f = rgb.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    v = f.max(axis=2)
    mn = f.min(axis=2)
    d = v - mn
    s = np.where(v > 0, d / np.maximum(v, 1e-6) * 255, 0)
    dd = np.maximum(d, 1e-6)
    h = np.where(v == r, (g - b) / dd * 60,
        np.where(v == g, (b - r) / dd * 60 + 120, (r - g) / dd * 60 + 240))
    h = np.where(d == 0, 0, np.mod(h, 360)) / 2
    return h, s, v


def color_mask(rgb: np.ndarray, valid: np.ndarray, sensitivity: int) -> np.ndarray:
    """Жёлто-оранжевые пиксели; sensitivity 0..100 расширяет диапазон (как в CuscutaDetector)."""
    h_min = max(5, 25 - sensitivity / 5.0)
    h_max = min(60, 35 + sensitivity / 5.0)
    sv_min = max(20, 200 - sensitivity * 1.8)
    h, s, v = _rgb_to_hsv_cv(rgb)
    return (h >= int(h_min)) & (h <= int(h_max)) & (s >= int(sv_min)) & (v >= int(sv_min)) & valid


def apply_edits(mask: np.ndarray, valid: np.ndarray, edits: np.ndarray | None) -> np.ndarray:
    """Ручные правки кистью: 1 — добавить к маске, 2 — стереть. edits может быть другого размера."""
    if edits is None or not edits.any():
        return mask
    h, w = mask.shape
    eh, ew = edits.shape
    e = edits[(np.arange(h) * eh // h)[:, None], (np.arange(w) * ew // w)[None, :]]
    return np.where(e == 1, valid, np.where(e == 2, False, mask))


def utm_crs(lon: float, lat: float) -> str:
    zone = int((lon + 180) // 6) + 1
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


def detect(path: str, sensitivity: int, *, resolution_m: float = 0.5, min_area_m2: float = 12.5,
           buffer_m: float = 2.0, fill_holes: bool = True,
           edits: np.ndarray | None = None) -> list[Candidate]:
    with rasterio.open(path) as src:
        if src.crs is None:
            raise ValueError("У снимка нет системы координат")
        if src.crs.is_geographic:
            lat = (src.bounds.top + src.bounds.bottom) / 2
            px_m = abs(src.transform.a) * 111320 * math.cos(math.radians(lat))
        else:
            px_m = abs(src.transform.a)
        scale = min(1.0, px_m / resolution_m)
        out_shape = (max(1, int(src.height * scale)), max(1, int(src.width * scale)))
        rgb, valid = _read_rgb(src, out_shape)
        transform = src.transform * src.transform.scale(src.width / out_shape[1], src.height / out_shape[0])
        src_crs = src.crs
        cx, cy = (src.bounds.left + src.bounds.right) / 2, (src.bounds.top + src.bounds.bottom) / 2

    mask = apply_edits(color_mask(rgb, valid, sensitivity), valid, edits).astype(np.uint8)
    real_res = resolution_m if scale < 1.0 else px_m
    min_px = max(1, int(round(min_area_m2 / (real_res ** 2))))
    mask = sieve(mask, size=min_px, connectivity=4)

    center = transform_geom(src_crs, "EPSG:4326", {"type": "Point", "coordinates": (cx, cy)})["coordinates"]
    metric = utm_crs(*center)
    polys = [shape(transform_geom(src_crs, metric, geom))
             for geom, val in shapes(mask, mask=mask == 1, transform=transform) if val == 1]
    if not polys:
        return []

    merged = unary_union([p.buffer(buffer_m) if buffer_m > 0 else p for p in polys])
    parts = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]

    out = []
    for p in parts:
        if fill_holes:
            p = Polygon(p.exterior)
        p = p.simplify(min(0.25, resolution_m / 2), preserve_topology=True)
        if p.is_empty or p.area < min_area_m2:
            continue
        geo = shape(transform_geom(metric, "EPSG:4326", mapping(p)))
        out.append(Candidate(geo, p.area / 10000))
    out.sort(key=lambda c: -c.area_ha)
    return out


def to_preview_pixels(poly4326: Polygon, preview: Preview) -> list[tuple[float, float]]:
    """Вершины полигона в пикселях превью (для отрисовки)."""
    geom = transform_geom("EPSG:4326", preview.crs, mapping(poly4326)) if preview.crs.to_epsg() != 4326 \
        else mapping(poly4326)
    inv = ~preview.transform
    return [inv * (x, y) for x, y in geom["coordinates"][0]]

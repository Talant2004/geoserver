#!/usr/bin/env python3
"""Synthetic RGB GeoTIFF (EPSG:4326) of a field with weed patches, for testing ingest.

  make_test_ortho.py out.tif [--lon 76.90 --lat 43.25 --width 1000 --height 800 --res 0.000005]
"""
import argparse
import math
import random
import struct


def render(width: int, height: int, seed: int = 7) -> bytes:
    rnd = random.Random(seed)
    patches = [(rnd.uniform(0.1, 0.9) * width, rnd.uniform(0.1, 0.9) * height, rnd.uniform(25, 70))
               for _ in range(6)]
    px = bytearray(width * height * 3)
    i = 0
    for y in range(height):
        for x in range(width):
            row = 18 if (x // 6) % 2 else 0
            r, g, b = 70 + row, 135 + row, 55
            for cx, cy, rad in patches:
                d = math.hypot(x - cx, y - cy)
                if d < rad * (0.8 + 0.2 * math.sin(x * 0.07 + y * 0.05)):
                    r, g, b = 196, 168, 70
                    break
            n = rnd.randint(-12, 12)
            px[i:i + 3] = bytes((max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n))))
            i += 3
    return bytes(px)


def write_geotiff(path: str, width: int, height: int, pixels: bytes, lon: float, lat: float, res: float) -> None:
    geokeys = [1, 1, 0, 3, 1024, 0, 1, 2, 1025, 0, 1, 1, 2048, 0, 1, 4326]
    extra = [
        (258, 3, [8, 8, 8]),
        (33550, 12, [res, res, 0.0]),
        (33922, 12, [0.0, 0.0, 0.0, lon, lat, 0.0]),
        (34735, 3, geokeys),
    ]
    simple = {256: (4, width), 257: (4, height), 259: (3, 1), 262: (3, 2), 277: (3, 3),
              278: (4, height), 279: (4, len(pixels)), 284: (3, 1)}
    n_entries = len(simple) + len(extra) + 1
    ifd_offset = 8
    data_offset = ifd_offset + 2 + n_entries * 12 + 4

    blobs = b""
    entries = []
    for tag, typ, values in extra:
        fmt = {3: "H", 12: "d"}[typ]
        blob = struct.pack(f"<{len(values)}{fmt}", *values)
        entries.append((tag, typ, len(values), data_offset + len(blobs)))
        blobs += blob + (b"\0" if len(blob) % 2 else b"")
    strip_offset = data_offset + len(blobs)
    entries.append((273, 4, 1, strip_offset))
    for tag, (typ, val) in simple.items():
        entries.append((tag, typ, 1, val))
    entries.sort()

    with open(path, "wb") as f:
        f.write(b"II*\0" + struct.pack("<I", ifd_offset))
        f.write(struct.pack("<H", n_entries))
        for tag, typ, count, val in entries:
            if typ == 3 and count == 1:
                f.write(struct.pack("<HHIHH", tag, typ, count, val, 0))
            else:
                f.write(struct.pack("<HHII", tag, typ, count, val))
        f.write(struct.pack("<I", 0))
        f.write(blobs)
        f.write(pixels)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("out")
    p.add_argument("--lon", type=float, default=76.90, help="долгота левого верхнего угла")
    p.add_argument("--lat", type=float, default=43.25, help="широта левого верхнего угла")
    p.add_argument("--width", type=int, default=1000)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--res", type=float, default=0.000005, help="размер пикселя, градусы")
    a = p.parse_args()
    write_geotiff(a.out, a.width, a.height, render(a.width, a.height), a.lon, a.lat, a.res)
    print(f"{a.out}: {a.width}x{a.height}, {a.lon},{a.lat} … "
          f"{a.lon + a.width * a.res:.6f},{a.lat - a.height * a.res:.6f}")


if __name__ == "__main__":
    main()

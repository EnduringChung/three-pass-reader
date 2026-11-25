"""Generate app.ico — no Pillow in this venv, so the raster is drawn by hand and PNG-encoded with zlib.

Design: a dark rounded tile with three bars of decreasing width (three passes, narrowing attention).
Rendered at 4x and box-downsampled for clean edges, then wrapped in a PNG-compressed .ico, which
Windows Vista and later read directly.
"""
import struct
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)
SS = 4                                   # supersampling factor
BG = (26, 32, 45)                        # tile
BARS = ((127, 178, 255), (95, 155, 240), (79, 140, 255))
TRANSPARENT = (0, 0, 0, 0)


def _rounded_alpha(x, y, w, h, radius):
    """1 inside a rounded rectangle, 0 outside, with 1px feather."""
    cx, cy = min(max(x, radius), w - radius), min(max(y, radius), h - radius)
    d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    return 1.0 if d <= radius else 0.0


def render(size):
    w = h = size * SS
    radius = w * 0.20
    # three capsules, centred and evenly spaced: widths shrink as the passes narrow attention
    bar_h = 0.115 * h
    r = bar_h / 2
    widths = (0.60, 0.46, 0.32)
    centres = (0.315, 0.5, 0.685)
    capsule = []
    for frac_w, cy in zip(widths, centres):
        w_bar = frac_w * w
        x0 = (w - w_bar) / 2
        capsule.append((x0 + r, x0 + w_bar - r, cy * h))
    px = []
    for y in range(h):
        row = []
        for x in range(w):
            inside = _rounded_alpha(x + 0.5, y + 0.5, w, h, radius)
            if inside == 0.0:
                row.append(TRANSPARENT)
                continue
            colour = BG
            for i, (sx0, sx1, cy) in enumerate(capsule):
                dx = min(max(x + 0.5, sx0), sx1)          # nearest point on the spine
                dy = y + 0.5 - cy
                if (x + 0.5 - dx) ** 2 + dy ** 2 <= r * r:
                    colour = BARS[i]
                    break
            row.append((colour[0], colour[1], colour[2], 255))
        px.append(row)
    # box downsample back to `size`
    out = []
    for y in range(size):
        row = []
        for x in range(size):
            r = g = b = a = 0
            for dy in range(SS):
                for dx in range(SS):
                    pr, pg, pb, pa = px[y * SS + dy][x * SS + dx]
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
            n = SS * SS
            row.append((r // max(a, 1), g // max(a, 1), b // max(a, 1), a // n))
        out.append(row)
    return out


def png_bytes(pixels):
    size = len(pixels)
    raw = b"".join(b"\x00" + b"".join(bytes(p[:4]) for p in row) for row in pixels)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def build(path=Path(__file__).with_name("app.ico")):
    images = [(s, png_bytes(render(s))) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", 0 if size >= 256 else size,
                               0 if size >= 256 else size, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    path.write_bytes(header + entries + blobs)
    return path, path.stat().st_size


if __name__ == "__main__":
    p, n = build()
    print(f"wrote {p} ({n} bytes, {len(SIZES)} sizes)")

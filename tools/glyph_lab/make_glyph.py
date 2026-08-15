#!/usr/bin/env python3
"""Create a deterministic, lossless PNG/SVG reference image for a Bangla glyph.

This is an isolated experiment. It does not modify the decoder or learned map.
The PNG is generated from the saved SVG with a fixed canvas, so the saved PNG
can be compared byte-for-byte/pixel-for-pixel by match_glyph.py.

Requirements:
    pip install fonttools uharfbuzz cairosvg pillow

Example:
    python tools/glyph_lab/make_glyph.py --font extracted.ttf --text "শ্চ" --name shcho
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
from html import escape

import uharfbuzz as hb
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

CANVAS = 256
PAD = 12


def shape(font_bytes: bytes, text: str) -> list[int]:
    face = hb.Face(font_bytes)
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction = "ltr"
    buf.script = "beng"
    buf.language = "bn"
    hb.shape(font, buf)
    return [int(info.codepoint) for info in buf.glyph_infos]


def build_svg(font: TTFont, gids: list[int]) -> str:
    order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    hmtx = font["hmtx"]
    upm = float(font["head"].unitsPerEm)

    cursor = 0.0
    bounds = None
    for gid in gids:
        name = order[gid]
        pen = BoundsPen(glyph_set)
        glyph_set[name].draw(pen)
        if pen.bounds:
            x0, y0, x1, y1 = pen.bounds
            b = (x0 + cursor, y0, x1 + cursor, y1)
            if bounds is None:
                bounds = b
            else:
                bounds = (
                    min(bounds[0], b[0]), min(bounds[1], b[1]),
                    max(bounds[2], b[2]), max(bounds[3], b[3]),
                )
        cursor += float(hmtx[name][0])

    if bounds is None:
        raise ValueError("The glyph has no drawable outline")

    min_x, min_y, max_x, max_y = bounds
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    scale = min((CANVAS - 2 * PAD) / width, (CANVAS - 2 * PAD) / height)
    tx = (CANVAS - width * scale) / 2.0 - min_x * scale
    ty = (CANVAS + height * scale) / 2.0 + min_y * scale

    paths = []
    cursor = 0.0
    for gid in gids:
        name = order[gid]
        pen = SVGPathPen(glyph_set)
        glyph_set[name].draw(pen)
        d = pen.getCommands()
        paths.append(
            f'<path d="{escape(d, quote=True)}" '
            f'transform="translate({tx + cursor * scale:.6f},{ty:.6f}) '
            f'scale({scale:.8f},{-scale:.8f})" fill="#000000"/>'
        )
        cursor += float(hmtx[name][0])

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS}" height="{CANVAS}" '
        f'viewBox="0 0 {CANVAS} {CANVAS}">' 
        f'<rect width="{CANVAS}" height="{CANVAS}" fill="#ffffff"/>'
        f'{"".join(paths)}</svg>\n'
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--font", required=True, type=Path, help="The exact embedded PDF TTF extracted from the PDF")
    ap.add_argument("--text", required=True, help="Bangla character/conjunct to render")
    ap.add_argument("--name", help="Output basename; default is Unicode code points")
    ap.add_argument("--out", type=Path, default=Path("glyph_lab"), help="Output directory")
    args = ap.parse_args()

    font_bytes = args.font.read_bytes()
    font_sha = hashlib.sha256(font_bytes).hexdigest()
    gids = shape(font_bytes, args.text)

    with TTFont(io.BytesIO(font_bytes), lazy=False) as font:
        svg = build_svg(font, gids)

    args.out.mkdir(parents=True, exist_ok=True)
    name = args.name or "_".join(f"u{ord(c):04x}" for c in args.text)
    svg_path = args.out / f"{name}.svg"
    png_path = args.out / f"{name}.png"
    meta_path = args.out / f"{name}.json"

    svg_path.write_text(svg, encoding="utf-8", newline="\n")

    try:
        import cairosvg
    except ImportError:
        raise SystemExit("Missing cairosvg. Install with: pip install cairosvg")

    cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(png_path), output_width=CANVAS, output_height=CANVAS)

    metadata = {
        "text": args.text,
        "gids": gids,
        "font_sha256": font_sha,
        "canvas": [CANVAS, CANVAS],
        "background": "#ffffff",
        "foreground": "#000000",
        "format": "PNG lossless",
    }
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"TEXT: {args.text}")
    print(f"GIDS: {gids}")
    print(f"FONT SHA256: {font_sha}")
    print(f"SVG: {svg_path}")
    print(f"PNG: {png_path}")
    print(f"META: {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

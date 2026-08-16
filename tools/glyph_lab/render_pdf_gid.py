#!/usr/bin/env python3
"""Render an exact font GID from the supplied TTF to deterministic SVG/PNG.

This deliberately bypasses Unicode shaping. It is for comparing a PDF's
extracted CID/GID outline with a Unicode candidate rendered from the same font.

Example:
  python tools/glyph_lab/render_pdf_gid.py --font fonts/DGRDOD_Bangla.ttf --gid 340 --name pdf_gid_340
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

CANVAS = 256
PAD = 16


def render(font: TTFont, gid: int) -> str:
    order = font.getGlyphOrder()
    if gid < 0 or gid >= len(order):
        raise ValueError(f"GID {gid} outside font range 0..{len(order)-1}")
    glyph_set = font.getGlyphSet()
    name = order[gid]
    pen = BoundsPen(glyph_set)
    glyph_set[name].draw(pen)
    if not pen.bounds:
        raise ValueError(f"GID {gid} ({name}) has no drawable outline")
    x0, y0, x1, y1 = pen.bounds
    w, h = max(x1-x0, 1.0), max(y1-y0, 1.0)
    scale = min((CANVAS-2*PAD)/w, (CANVAS-2*PAD)/h)
    tx = (CANVAS-w*scale)/2 - x0*scale
    ty = (CANVAS+h*scale)/2 + y0*scale
    path_pen = SVGPathPen(glyph_set)
    glyph_set[name].draw(path_pen)
    d = path_pen.getCommands()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS}" height="{CANVAS}" viewBox="0 0 {CANVAS} {CANVAS}">'
            f'<rect width="{CANVAS}" height="{CANVAS}" fill="#fff"/>'
            f'<path d="{escape(d, quote=True)}" transform="translate({tx:.6f},{ty:.6f}) scale({scale:.8f},{-scale:.8f})" fill="#000"/>'
            f'</svg>\n')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--font', required=True, type=Path)
    ap.add_argument('--gid', required=True, type=int)
    ap.add_argument('--name', default=None)
    ap.add_argument('--out', type=Path, default=Path('glyph_lab'))
    args = ap.parse_args()
    data = args.font.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    with TTFont(str(args.font), lazy=False) as font:
        order = font.getGlyphOrder()
        svg = render(font, args.gid)
        glyph_name = order[args.gid]
    args.out.mkdir(parents=True, exist_ok=True)
    name = args.name or f'pdf_gid_{args.gid}'
    svg_path = args.out / f'{name}.svg'
    png_path = args.out / f'{name}.png'
    meta_path = args.out / f'{name}.json'
    svg_path.write_text(svg, encoding='utf-8', newline='\n')
    try:
        import cairosvg
    except ImportError:
        raise SystemExit('Missing cairosvg. Install with: pip install cairosvg')
    cairosvg.svg2png(bytestring=svg.encode('utf-8'), write_to=str(png_path), output_width=CANVAS, output_height=CANVAS)
    meta_path.write_text(json.dumps({'source':'PDF GID','gid':args.gid,'glyph_name':glyph_name,'font_sha256':sha,'canvas':[CANVAS,CANVAS]}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'GID: {args.gid}')
    print(f'GLYPH NAME: {glyph_name}')
    print(f'FONT SHA256: {sha}')
    print(f'SVG: {svg_path}')
    print(f'PNG: {png_path}')
    print(f'META: {meta_path}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

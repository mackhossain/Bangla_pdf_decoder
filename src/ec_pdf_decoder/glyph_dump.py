"""Dump unresolved PDF glyphs as deterministic red SVG files."""
from __future__ import annotations

from html import escape
from pathlib import Path
import tempfile

from .direct_pdf_fixed import embedded_fonts


def _gid_svg(raw_font: bytes, gid: int) -> str:
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    canvas, pad = 256, 16
    with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as tmp:
        tmp.write(raw_font)
        font_path = Path(tmp.name)
    try:
        font = TTFont(str(font_path), lazy=False)
        try:
            order = font.getGlyphOrder()
            if gid < 0 or gid >= len(order):
                raise ValueError(f"GID {gid} outside font range 0..{len(order)-1}")
            glyph_set = font.getGlyphSet()
            name = order[gid]
            bounds = BoundsPen(glyph_set)
            glyph_set[name].draw(bounds)
            if not bounds.bounds:
                # Keep a valid SVG so every dumped CID has a deterministic artifact.
                return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}" '
                        f'viewBox="0 0 {canvas} {canvas}"><rect width="100%" height="100%" fill="white"/>'
                        f'<text x="8" y="128" fill="#d11" font-size="18">GID {gid} has no outline</text></svg>\n')
            x0, y0, x1, y1 = bounds.bounds
            width, height = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
            scale = min((canvas - 2 * pad) / width, (canvas - 2 * pad) / height)
            tx = (canvas - width * scale) / 2 - x0 * scale
            ty = (canvas + height * scale) / 2 + y0 * scale
            pen = SVGPathPen(glyph_set)
            glyph_set[name].draw(pen)
            d = pen.getCommands()
            return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}" viewBox="0 0 {canvas} {canvas}">'
                    f'<rect width="100%" height="100%" fill="white"/>'
                    f'<path d="{escape(d, quote=True)}" transform="translate({tx:.6f},{ty:.6f}) scale({scale:.8f},{-scale:.8f})" fill="#d11"/>'
                    f'</svg>\n')
        finally:
            font.close()
    finally:
        try:
            font_path.unlink(missing_ok=True)
        except OSError:
            pass


def dump_gids(pdf: bytes, page: int, gids: list[int], out_dir: Path) -> int:
    """Dump each unresolved CID/GID as a standalone red SVG."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = sorted(set(int(g) for g in gids))
    if not wanted:
        return 0
    for _resource, _base_font, raw in embedded_fonts(pdf, page):
        if raw:
            for gid in wanted:
                (out_dir / f"{gid}.svg").write_text(_gid_svg(raw, gid), encoding="utf-8", newline="\n")
            return len(wanted)
    return 0

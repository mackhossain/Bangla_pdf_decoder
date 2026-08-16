"""Dump unresolved PDF glyph outlines as standalone red SVG files."""
from __future__ import annotations

from html import escape
from pathlib import Path

from .direct_pdf_fixed import embedded_fonts


def _single_gid_svg(font_path: Path, gid: int) -> str:
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    canvas = 256
    pad = 16
    font = TTFont(str(font_path), lazy=False)
    try:
        order = font.getGlyphOrder()
        if gid < 0 or gid >= len(order):
            raise ValueError(f"GID {gid} outside font range")
        glyph_set = font.getGlyphSet()
        name = order[gid]
        bounds_pen = BoundsPen(glyph_set)
        glyph_set[name].draw(bounds_pen)
        if not bounds_pen.bounds:
            return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}" viewBox="0 0 {canvas} {canvas}"><rect width="{canvas}" height="{canvas}" fill="#fff"/><text x="8" y="128" fill="#d11" font-size="18">GID {gid} has no outline</text></svg>\n'''
        x0, y0, x1, y1 = bounds_pen.bounds
        width = max(x1 - x0, 1.0)
        height = max(y1 - y0, 1.0)
        scale = min((canvas - 2 * pad) / width, (canvas - 2 * pad) / height)
        tx = (canvas - width * scale) / 2 - x0 * scale
        ty = (canvas + height * scale) / 2 + y0 * scale
        path_pen = SVGPathPen(glyph_set)
        glyph_set[name].draw(path_pen)
        d = path_pen.getCommands()
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}" viewBox="0 0 {canvas} {canvas}">'
            f'<rect width="{canvas}" height="{canvas}" fill="#fff"/>'
            f'<path d="{escape(d, quote=True)}" transform="translate({tx:.6f},{ty:.6f}) scale({scale:.8f},{-scale:.8f})" fill="#d11"/>'
            f'</svg>\n'
        )
    finally:
        font.close()


def dump_gids(pdf: bytes, page: int, gids: list[int], out_dir: Path) -> int:
    """Write one standalone red SVG per requested GID and return count written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = sorted(set(int(g) for g in gids))
    if not wanted:
        return 0
    written = 0
    for _resource, base_font, raw in embedded_fonts(pdf, page):
        if not raw:
            continue
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.ttf', delete=False) as tmp:
            tmp.write(raw)
            font_path = Path(tmp.name)
        try:
            for gid in wanted:
                path = out_dir / f"{gid}.svg"
                path.write_text(_single_gid_svg(font_path, gid), encoding="utf-8", newline="\n")
                written += 1
        finally:
            try:
                font_path.unlink(missing_ok=True)
            except OSError:
                pass
        break
    return written

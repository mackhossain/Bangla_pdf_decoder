"""Dump unresolved PDF glyphs and manually requested text using embedded PDF fonts."""
from __future__ import annotations

from html import escape
from io import BytesIO
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
    for _resource, _base_font, raw in embedded_fonts(pdf, page):
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


def _shape_text(font_bytes: bytes, text: str):
    """Shape Unicode text with the exact embedded PDF font using HarfBuzz."""
    import uharfbuzz as hb
    from fontTools.ttLib import TTFont

    face = hb.Face(font_bytes)
    font = hb.Font(face)
    with TTFont(BytesIO(font_bytes), lazy=False) as ttfont:
        upm = int(ttfont['head'].unitsPerEm)
    font.scale = (upm, upm)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction = 'ltr'
    buf.script = 'beng'
    buf.language = 'bn'
    hb.shape(font, buf)
    return buf.glyph_infos, buf.glyph_positions, upm


def _text_svg(font_bytes: bytes, text: str) -> tuple[str, list[int]]:
    """Render shaped Unicode text as deterministic SVG outlines."""
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    canvas = 256
    pad = 16
    infos, positions, upm = _shape_text(font_bytes, text)
    gids = [int(info.codepoint) for info in infos]

    with TTFont(BytesIO(font_bytes), lazy=False) as font:
        order = font.getGlyphOrder()
        glyph_set = font.getGlyphSet()
        cursor_x = 0.0
        cursor_y = 0.0
        bounds = None
        glyph_data = []
        for info, pos in zip(infos, positions):
            gid = int(info.codepoint)
            if gid < 0 or gid >= len(order):
                continue
            name = order[gid]
            x_offset = float(pos.x_offset)
            y_offset = float(pos.y_offset)
            pen = BoundsPen(glyph_set)
            glyph_set[name].draw(pen)
            if pen.bounds:
                x0, y0, x1, y1 = pen.bounds
                bx0 = x0 + cursor_x + x_offset
                by0 = y0 + cursor_y + y_offset
                bx1 = x1 + cursor_x + x_offset
                by1 = y1 + cursor_y + y_offset
                if bounds is None:
                    bounds = (bx0, by0, bx1, by1)
                else:
                    bounds = (
                        min(bounds[0], bx0), min(bounds[1], by0),
                        max(bounds[2], bx1), max(bounds[3], by1),
                    )
            glyph_data.append((name, cursor_x + x_offset, cursor_y + y_offset))
            cursor_x += float(pos.x_advance)
            cursor_y += float(pos.y_advance)

        if bounds is None:
            raise ValueError(f'embedded PDF font cannot draw {text!r}')

        min_x, min_y, max_x, max_y = bounds
        width = max(max_x - min_x, 1.0)
        height = max(max_y - min_y, 1.0)
        scale = min((canvas - 2 * pad) / width, (canvas - 2 * pad) / height)
        tx = (canvas - width * scale) / 2.0 - min_x * scale
        ty = (canvas + height * scale) / 2.0 + min_y * scale

        paths = []
        for name, x, y in glyph_data:
            pen = SVGPathPen(glyph_set)
            glyph_set[name].draw(pen)
            d = pen.getCommands()
            paths.append(
                f'<path d="{escape(d, quote=True)}" '
                f'transform="translate({tx + x * scale:.6f},{ty + y * scale:.6f}) '
                f'scale({scale:.8f},{-scale:.8f})" fill="#d11"/>'
            )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}" '
        f'viewBox="0 0 {canvas} {canvas}">'
        f'<rect width="{canvas}" height="{canvas}" fill="#fff"/>'
        f'{"".join(paths)}</svg>\n'
    )
    return svg, gids


def dump_text_glyph(pdf: bytes, page: int, text: str, out_dir: Path) -> Path:
    """Render manually supplied text with the PDF page's embedded font."""
    if not text:
        raise ValueError('genglyph text cannot be empty')
    out_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for resource, base_font, raw in embedded_fonts(pdf, page):
        if not raw:
            continue
        try:
            svg, gids = _text_svg(raw, text)
            path = out_dir / f'{text}.svg'
            path.write_text(svg, encoding='utf-8', newline='\n')
            return path
        except Exception as exc:
            errors.append(f'{resource.decode("latin1", errors="replace")}/{base_font}: {exc}')
    detail = '; '.join(errors) if errors else 'no embedded FontFile2 resource found on the selected page'
    raise ValueError(f'could not render {text!r} from the PDF embedded font: {detail}')

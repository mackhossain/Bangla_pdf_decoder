"""PDF glyph dumping plus exact reference-font artwork for --genglyph."""
from __future__ import annotations

import io
from html import escape
from pathlib import Path

from .direct_pdf_fixed import embedded_fonts


def _reference_font_paths(explicit: Path | None = None) -> list[Path]:
    paths = []
    if explicit:
        paths.append(Path(explicit))
    paths.extend([
        Path("tools/glyph_lab/DGRDOD_Bangla.ttf"),
        Path("tools/glyph_lab/glyph_lab/DGRDOD_Bangla.ttf"),
        Path("experiment/fonts/DGRDOD_Bangla.ttf"),
        Path("DGRDOD_Bangla.ttf"),
    ])
    out = []
    seen = set()
    for p in paths:
        if not p.exists():
            continue
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _single_gid_svg(font_path: Path, gid: int) -> str:
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    font = TTFont(str(font_path), lazy=False)
    try:
        order = font.getGlyphOrder()
        if not 0 <= gid < len(order):
            raise ValueError(f"GID {gid} outside font range")
        glyph_set = font.getGlyphSet()
        name = order[gid]
        bounds = BoundsPen(glyph_set)
        glyph_set[name].draw(bounds)
        if not bounds.bounds:
            raise ValueError(f"GID {gid} ({name}) has no drawable outline")
        x0, y0, x1, y1 = bounds.bounds
        pad = 16.0
        size = 512.0
        sx = (size - 2 * pad) / max(x1 - x0, 1.0)
        sy = (size - 2 * pad) / max(y1 - y0, 1.0)
        scale = min(sx, sy)
        tx = (size - (x1 - x0) * scale) / 2 - x0 * scale
        ty = (size + (y1 - y0) * scale) / 2 + y0 * scale
        pen = SVGPathPen(glyph_set)
        glyph_set[name].draw(pen)
        d = pen.getCommands()
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(size)}" height="{int(size)}" '
            f'viewBox="0 0 {int(size)} {int(size)}" shape-rendering="geometricPrecision">'
            f'<rect width="100%" height="100%" fill="white"/>'
            f'<path d="{escape(d, quote=True)}" transform="translate({tx:.6f},{ty:.6f}) '
            f'scale({scale:.8f},{-scale:.8f})" fill="#d11"/></svg>\n'
        )
    finally:
        font.close()


def dump_gids(pdf: bytes, page: int, gids: list[int], out_dir: Path) -> int:
    """Dump the requested PDF embedded-font GIDs as red SVGs named by GID/CID."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = sorted(set(int(g) for g in gids))
    if not wanted:
        return 0
    for _resource, _base_font, raw in embedded_fonts(pdf, page):
        if not raw:
            continue
        import tempfile
        font_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as tmp:
                tmp.write(raw)
                font_path = Path(tmp.name)
            count = 0
            for gid in wanted:
                (out_dir / f"{gid}.svg").write_text(
                    _single_gid_svg(font_path, gid), encoding="utf-8", newline="\n"
                )
                count += 1
            return count
        finally:
            if font_path is not None:
                try:
                    font_path.unlink(missing_ok=True)
                except OSError:
                    pass
    return 0


def _shape_with_positions(font_bytes: bytes, text: str):
    """Return HarfBuzz shaped GIDs and exact glyph offsets/advances."""
    import uharfbuzz as hb
    from fontTools.ttLib import TTFont

    face = hb.Face(font_bytes)
    hb_font = hb.Font(face)
    with TTFont(io.BytesIO(font_bytes), lazy=False) as ttf:
        upm = int(ttf["head"].unitsPerEm)
    hb_font.scale = (upm, upm)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction = "ltr"
    buf.script = "beng"
    buf.language = "bn"
    hb.shape(hb_font, buf)
    gids = [int(i.codepoint) for i in buf.glyph_infos]
    pos = [
        (int(p.x_offset), int(p.y_offset), int(p.x_advance), int(p.y_advance))
        for p in buf.glyph_positions
    ]
    return gids, pos, upm


def _reference_text_svg(reference_font: Path, text: str, pixel_size: int = 512) -> tuple[str, list[int]]:
    """Render the exact full-font shaped text to a tightly cropped SVG.

    The SVG uses the real OpenType outlines and real HarfBuzz offsets/advances.
    There is deliberately no fixed 256x256 glyph box around the result.
    """
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    raw = reference_font.read_bytes()
    gids, positions, upm = _shape_with_positions(raw, text)
    font = TTFont(io.BytesIO(raw), lazy=False)
    try:
        glyph_set = font.getGlyphSet()
        order = font.getGlyphOrder()
        pen_x = 0.0
        pen_y = 0.0
        parts_meta = []
        overall = None

        for gid, (xoff, yoff, xadv, yadv) in zip(gids, positions):
            if not 0 <= gid < len(order):
                raise ValueError(f"reference GID {gid} outside font range")
            name = order[gid]
            bpen = BoundsPen(glyph_set)
            glyph_set[name].draw(bpen)
            dx = pen_x + xoff
            dy = pen_y + yoff
            if bpen.bounds:
                x0, y0, x1, y1 = bpen.bounds
                b = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
                overall = b if overall is None else (
                    min(overall[0], b[0]), min(overall[1], b[1]),
                    max(overall[2], b[2]), max(overall[3], b[3])
                )
                parts_meta.append((name, dx, dy))
            pen_x += xadv
            pen_y += yadv

        if overall is None:
            raise ValueError(f"no drawable outline produced for {text!r}; shaped GIDs={gids}")

        min_x, min_y, max_x, max_y = overall
        pad_units = max(upm * 0.03, 8.0)
        min_x -= pad_units
        min_y -= pad_units
        max_x += pad_units
        max_y += pad_units
        width_units = max(max_x - min_x, 1.0)
        height_units = max(max_y - min_y, 1.0)
        scale = min(pixel_size / width_units, pixel_size / height_units)
        out_w = max(1, int(round(width_units * scale)))
        out_h = max(1, int(round(height_units * scale)))

        paths = []
        for name, dx, dy in parts_meta:
            pen = SVGPathPen(glyph_set)
            glyph_set[name].draw(pen)
            d = pen.getCommands()
            tx = (dx - min_x) * scale
            ty = (max_y - dy) * scale
            paths.append(
                f'<path d="{escape(d, quote=True)}" transform="translate({tx:.6f},{ty:.6f}) '
                f'scale({scale:.8f},{-scale:.8f})" fill="#d11"/>'
            )

        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{out_w}" height="{out_h}" '
            f'viewBox="0 0 {out_w} {out_h}" shape-rendering="geometricPrecision">'
            f'<rect width="100%" height="100%" fill="white"/>'
            f'{"".join(paths)}</svg>\n'
        )
        return svg, gids
    finally:
        font.close()


def generate_reference_glyph(text: str, out_dir: Path, reference_font: Path | None = None):
    """Generate standalone reference SVG and PNG for the requested Bangla text."""
    fonts = _reference_font_paths(reference_font)
    if not fonts:
        raise FileNotFoundError(
            "DGRDOD_Bangla.ttf not found; expected tools/glyph_lab/DGRDOD_Bangla.ttf, "
            "tools/glyph_lab/glyph_lab/DGRDOD_Bangla.ttf, experiment/fonts/DGRDOD_Bangla.ttf, or DGRDOD_Bangla.ttf"
        )
    ref = fonts[0]
    svg, gids = _reference_text_svg(ref, text)
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = out_dir / f"{text}.svg"
    png_path = out_dir / f"{text}.png"
    svg_path.write_text(svg, encoding="utf-8", newline="\n")
    try:
        import cairosvg
        cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(png_path))
    except Exception:
        png_path = None

    print(f"GENGLYPH REFERENCE: {text!r}")
    print(f"REFERENCE FONT: {ref}")
    print(f"HARFBUZZ GIDS: {gids}")
    print(f"SVG: {svg_path.resolve()}")
    if png_path is not None:
        print(f"PNG: {png_path.resolve()}")
    else:
        print("PNG: unavailable (CairoSVG not installed)")
    return svg_path


def dump_text_glyph(
    pdf: bytes,
    page: int,
    text: str,
    out_dir: Path,
    *,
    mapping_path: Path | None = None,
    learned_path: Path | None = None,
    reference_font: Path | None = None,
) -> Path:
    """Generate the standalone reference artwork for --genglyph.

    The PDF is intentionally not used to invent a CID here. The generated SVG/PNG
    is the exact full-reference-font rendering that can later be compared against
    the PDF's red glyph candidates.
    """
    if not text:
        raise ValueError("genglyph text cannot be empty")
    return generate_reference_glyph(text, out_dir, reference_font)

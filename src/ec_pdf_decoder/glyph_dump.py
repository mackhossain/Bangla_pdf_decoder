"""Dump PDF glyphs and use locally curated exact reference SVGs for --genglyph."""
from __future__ import annotations

import io
import json
import shutil
import tempfile
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
            raise ValueError(f"GID {gid} ({name}) has no drawable outline")
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
            f'<rect width="{canvas}" height="{canvas}" fill="white"/>'
            f'<path d="{escape(d, quote=True)}" transform="translate({tx:.6f},{ty:.6f}) scale({scale:.8f},{-scale:.8f})" fill="#d11"/>'
            f'</svg>\n'
        )
    finally:
        font.close()


def dump_gids(pdf: bytes, page: int, gids: list[int], out_dir: Path) -> int:
    """Dump requested PDF embedded-font GIDs as standalone red SVGs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = sorted(set(int(g) for g in gids))
    if not wanted:
        return 0
    for _resource, _base_font, raw in embedded_fonts(pdf, page):
        if not raw:
            continue
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


def _reference_candidates(text: str) -> list[Path]:
    """Find a user-created exact reference SVG for the requested text."""
    return [
        Path("tools/glyph_lab/references") / f"{text}.svg",
        Path("tools/glyph_lab/glyph_lab/references") / f"{text}.svg",
        Path("experiment/glyph-lab/references") / f"{text}.svg",
        Path("references") / f"{text}.svg",
    ]


def _copy_exact_reference(text: str, out_dir: Path) -> Path | None:
    """Use an existing PDF-derived reference SVG verbatim when present."""
    for src in _reference_candidates(text):
        if src.exists() and src.is_file():
            out_dir.mkdir(parents=True, exist_ok=True)
            dst = out_dir / f"{text}.svg"
            shutil.copyfile(src, dst)
            print(f"GENGLYPH REFERENCE: using exact curated PDF SVG {src}")
            print(f"SVG: {dst.resolve()}")
            return dst
    return None


def _reference_font_paths(explicit: Path | None = None) -> list[Path]:
    paths: list[Path] = []
    if explicit is not None:
        paths.append(Path(explicit))
    paths.extend([
        Path("tools/glyph_lab/DGRDOD_Bangla.ttf"),
        Path("tools/glyph_lab/glyph_lab/DGRDOD_Bangla.ttf"),
        Path("experiment/fonts/DGRDOD_Bangla.ttf"),
        Path("DGRDOD_Bangla.ttf"),
    ])
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _shape_with_positions(font_bytes: bytes, text: str):
    """Return HarfBuzz GIDs, positions and UPM."""
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


def generate_reference_glyph(text: str, out_dir: Path, reference_font: Path | None = None) -> Path:
    """Generate a standalone reference SVG only when no curated PDF SVG exists."""
    curated = _copy_exact_reference(text, out_dir)
    if curated is not None:
        return curated

    fonts = _reference_font_paths(reference_font)
    if not fonts:
        raise FileNotFoundError(
            f"no exact curated reference SVG for {text!r}, and DGRDOD_Bangla.ttf was not found"
        )

    ref = fonts[0]
    raw = ref.read_bytes()
    gids, positions, upm = _shape_with_positions(raw, text)

    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(raw), lazy=False)
    try:
        glyph_set = font.getGlyphSet()
        order = font.getGlyphOrder()
        pen_x = pen_y = 0.0
        meta = []
        overall = None
        for gid, (xoff, yoff, xadv, yadv) in zip(gids, positions):
            name = order[gid]
            pen = BoundsPen(glyph_set)
            glyph_set[name].draw(pen)
            dx, dy = pen_x + xoff, pen_y + yoff
            if pen.bounds:
                x0, y0, x1, y1 = pen.bounds
                b = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
                overall = b if overall is None else (
                    min(overall[0], b[0]), min(overall[1], b[1]),
                    max(overall[2], b[2]), max(overall[3], b[3])
                )
                meta.append((name, dx, dy))
            pen_x += xadv
            pen_y += yadv

        if overall is None:
            raise ValueError(f"no drawable outline produced for {text!r}; shaped GIDs={gids}")

        min_x, min_y, max_x, max_y = overall
        pad_units = max(upm * 0.03, 8.0)
        min_x -= pad_units; min_y -= pad_units
        max_x += pad_units; max_y += pad_units
        width_units = max(max_x - min_x, 1.0)
        height_units = max(max_y - min_y, 1.0)
        scale = min(512.0 / width_units, 512.0 / height_units)
        out_w = max(1, int(round(width_units * scale)))
        out_h = max(1, int(round(height_units * scale)))

        paths = []
        for name, dx, dy in meta:
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
            f'<rect width="100%" height="100%" fill="white"/>{"".join(paths)}</svg>\n'
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{text}.svg"
        path.write_text(svg, encoding="utf-8", newline="\n")
        print(f"GENGLYPH REFERENCE: generated from {ref}")
        print(f"HARFBUZZ GIDS: {gids}")
        print(f"SVG: {path.resolve()}")
        return path
    finally:
        font.close()


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
    """Generate/use the standalone reference artwork for --genglyph.

    When the user has already curated an exact PDF-derived reference SVG, use it
    verbatim. This avoids Unicode shaping or font substitution changing the target.
    """
    if not text:
        raise ValueError("genglyph text cannot be empty")
    return generate_reference_glyph(text, out_dir, reference_font)


__all__ = ["dump_gids", "dump_text_glyph", "generate_reference_glyph"]

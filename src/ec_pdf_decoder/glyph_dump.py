"""Dump unresolved PDF glyphs and generate exact reference glyph artwork."""
from __future__ import annotations

import json
import io
from html import escape
from pathlib import Path

from .direct_pdf_fixed import embedded_fonts
from .learned_mapping import font_key, glyph_fingerprint_map, glyph_key, load_database


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
            f'<rect width="{canvas}" height="{canvas}" fill="#fff"/>'
            f'<path d="{escape(d, quote=True)}" transform="translate({tx:.6f},{ty:.6f}) scale({scale:.8f},{-scale:.8f})" fill="#d11"/>'
            f'</svg>\n'
        )
    finally:
        font.close()


def dump_gids(pdf: bytes, page: int, gids: list[int], out_dir: Path) -> int:
    """Write one standalone red SVG per requested PDF GID and return count written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = sorted(set(int(g) for g in gids))
    if not wanted:
        return 0
    written = 0
    for _resource, _base_font, raw in embedded_fonts(pdf, page):
        if not raw:
            continue
        import tempfile
        font_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.ttf', delete=False) as tmp:
                tmp.write(raw)
                font_path = Path(tmp.name)
            for gid in wanted:
                path = out_dir / f"{gid}.svg"
                path.write_text(_single_gid_svg(font_path, gid), encoding="utf-8", newline="\n")
                written += 1
        finally:
            if font_path is not None:
                try:
                    font_path.unlink(missing_ok=True)
                except OSError:
                    pass
        break
    return written


def _unique_gids(values: list[int], text: str) -> int | None:
    wanted = sorted(set(int(g) for g in values))
    if len(wanted) == 1:
        return wanted[0]
    if len(wanted) > 1:
        raise ValueError(f"embedded PDF font has multiple exact GID candidates for {text!r}: {wanted}")
    return None


def _mapping_text_gid(mapping_path: Path | None, base_font: str, text: str) -> int | None:
    """Find a GID explicitly mapped to text in the custom mapping database."""
    if mapping_path is None or not mapping_path.exists():
        return None
    try:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    sections = []
    candidates = [base_font, base_font.lstrip("/")]
    if "+" in base_font:
        candidates.append(base_font.split("+", 1)[1])
    for key in candidates:
        section = data.get(key)
        if isinstance(section, dict):
            sections.append(section)
    if not sections:
        normalized = base_font.lstrip("/").split("+", 1)[-1]
        for key, section in data.items():
            if isinstance(key, str) and key.lstrip("/").split("+", 1)[-1] == normalized and isinstance(section, dict):
                sections.append(section)
    gids = []
    for section in sections:
        for key, value in section.items():
            mapped = value if isinstance(value, str) else value.get("text") if isinstance(value, dict) else None
            if mapped == text:
                try:
                    gids.append(int(key))
                except (TypeError, ValueError):
                    pass
    return _unique_gids(gids, text)


def _learned_text_gid(learned_path: Path | None, raw: bytes, base_font: str, text: str) -> int | None:
    """Find a confirmed GID for the exact embedded-font identity and text."""
    if learned_path is None or not learned_path.exists():
        return None
    try:
        data = load_database(learned_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    fkey = font_key(raw, base_font)
    section = data.get("fonts", {}).get(fkey, {})
    glyphs = section.get("glyphs", {}) if isinstance(section, dict) else {}
    gids = []
    for key, entry in glyphs.items():
        if not isinstance(entry, dict) or entry.get("confidence", 0) < 1.0:
            continue
        if entry.get("text") == text:
            try:
                gids.append(int(entry.get("gid", key)))
            except (TypeError, ValueError):
                pass
    return _unique_gids(gids, text)


def _font_exact_text_gid(raw: bytes, text: str) -> int | None:
    """Find an exact multi-codepoint glyph exposed by the embedded font itself."""
    from .direct_pdf import ttf_gid_map
    gids = [gid for gid, mapped in ttf_gid_map(raw).items() if mapped == text]
    return _unique_gids(gids, text)


def _harfbuzz_single_gid(raw: bytes, text: str) -> int | None:
    """Resolve logical Bangla text through the embedded font's own GSUB shaping.

    Only a one-GID result is accepted. A multi-GID shaping result is not an
    exact single PDF glyph, so it is deliberately rejected here.
    """
    try:
        from .bangla_harfbuzz import shape_gids
        shaped = shape_gids(raw, text)
    except Exception:
        return None
    if shaped is None or len(shaped) != 1:
        return None
    return int(shaped[0])


def _reference_font_paths(explicit: Path | None = None) -> list[Path]:
    """Return likely copies of the original/full DGRDOD Bangla font."""
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
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen or not path.exists():
            continue
        seen.add(key)
        out.append(path)
    return out


def _shape_with_positions(font_bytes: bytes, text: str) -> tuple[list[int], list[tuple[int, int, int, int]]]:
    """Shape text and return (GIDs, x/y offsets + advances) using real HarfBuzz positions."""
    import uharfbuzz as hb
    from fontTools.ttLib import TTFont

    face = hb.Face(font_bytes)
    font = hb.Font(face)
    with TTFont(io.BytesIO(font_bytes), lazy=False) as ttf:
        upm = int(ttf["head"].unitsPerEm)
    font.scale = (upm, upm)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction = "ltr"
    buf.script = "beng"
    buf.language = "bn"
    hb.shape(font, buf)
    infos = buf.glyph_infos
    poss = buf.glyph_positions
    gids = [int(info.codepoint) for info in infos]
    positions = [(int(p.x_offset), int(p.y_offset), int(p.x_advance), int(p.y_advance)) for p in poss]
    return gids, positions


def _reference_text_svg(reference_font: Path, text: str, size: int = 512, pad: float = 16.0) -> tuple[str, list[int]]:
    """Render a tightly cropped SVG from the full reference font using exact HarfBuzz positions."""
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont

    raw = reference_font.read_bytes()
    gids, positions = _shape_with_positions(raw, text)
    font = TTFont(io.BytesIO(raw), lazy=False)
    try:
        glyph_set = font.getGlyphSet()
        order = font.getGlyphOrder()
        upm = float(font["head"].unitsPerEm")
        cursor_x = 0.0
        cursor_y = 0.0
        bounds = None
        glyph_meta = []
        for gid, (xoff, yoff, xadv, yadv) in zip(gids, positions):
            if gid < 0 or gid >= len(order):
                raise ValueError(f"reference GID {gid} outside font range")
            name = order[gid]
            pen = BoundsPen(glyph_set)
            glyph_set[name].draw(pen)
            if pen.bounds:
                x0, y0, x1, y1 = pen.bounds
                dx = cursor_x + xoff
                dy = cursor_y + yoff
                b = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
                bounds = b if bounds is None else (
                    min(bounds[0], b[0]), min(bounds[1], b[1]),
                    max(bounds[2], b[2]), max(bounds[3], b[3])
                )
                glyph_meta.append((name, dx, dy))
            cursor_x += xadv
            cursor_y += yadv

        if bounds is None:
            raise ValueError(f"reference font produced no drawable outline for {text!r} (gids={gids})")

        min_x, min_y, max_x, max_y = bounds
        width_units = max(max_x - min_x, 1.0)
        height_units = max(max_y - min_y, 1.0)
        scale = min((size - 2 * pad) / width_units, (size - 2 * pad) / height_units)
        width = int(round(width_units * scale + 2 * pad))
        height = int(round(height_units * scale + 2 * pad))
        parts = []
        for name, dx, dy in glyph_meta:
            pen = SVGPathPen(glyph_set)
            glyph_set[name].draw(pen)
            d = pen.getCommands()
            tx = pad + (dx - min_x) * scale
            ty = pad + (max_y - dy) * scale
            parts.append(
                f'<path d="{escape(d, quote=True)}" transform="translate({tx:.5f},{ty:.5f}) scale({scale:.8f},{-scale:.8f})" fill="#d11"/>'
            )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" shape-rendering="geometricPrecision">'
            f'<path d="M0 0H{width}V{height}H0Z" fill="#fff"/>{"".join(parts)}</svg>\n'
        )
        return svg, gids
    finally:
        font.close()


def generate_reference_glyph(text: str, out_dir: Path, reference_font: Path | None = None) -> tuple[Path, Path | None, list[int], Path]:
    """Generate exact reference SVG/PNG artwork for manual text; no PDF glyph mapping is claimed."""
    fonts = _reference_font_paths(reference_font)
    if not fonts:
        raise FileNotFoundError("DGRDOD_Bangla.ttf not found; expected it in tools/glyph_lab or experiment/fonts")
    ref = fonts[0]
    svg, gids = _reference_text_svg(ref, text)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = text
    svg_path = out_dir / f"{safe_name}.svg"
    svg_path.write_text(svg, encoding="utf-8", newline="\n")
    png_path = None
    try:
        import cairosvg
        png_path = out_dir / f"{safe_name}.png"
        cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(png_path), output_width=512, output_height=512)
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
    return svg_path, png_path, gids, ref


def dump_text_glyph(
    pdf: bytes,
    page: int,
    text: str,
    out_dir: Path,
    *,
    mapping_path: Path | None = Path('custom_glyph_map.json'),
    learned_path: Path | None = Path('learned_glyph_map.json'),
    reference_font: Path | None = None,
) -> Path:
    """Generate the manual reference artwork first, then use confirmed PDF mapping if available.

    For ambiguous subset fonts, the reference SVG/PNG is still generated so it can
    be matched visually outside the browser review box. The function never claims
    an arbitrary PDF CID is the answer.
    """
    if not text:
        raise ValueError('genglyph text cannot be empty')
    out_dir.mkdir(parents=True, exist_ok=True)

    # Always generate the actual requested Bangla artwork from the full reference font.
    ref_svg, _ref_png, _ref_gids, _ref_font = generate_reference_glyph(text, out_dir, reference_font)

    # If a confirmed exact PDF mapping already exists, keep the useful PDF-derived output too.
    errors = []
    for resource, base_font, raw in embedded_fonts(pdf, page):
        if not raw:
            continue
        try:
            gid = _learned_text_gid(learned_path, raw, base_font, text)
            if gid is None:
                gid = _mapping_text_gid(mapping_path, base_font, text)
            if gid is None:
                gid = _font_exact_text_gid(raw, text)
            if gid is None:
                gid = _harfbuzz_single_gid(raw, text)
            if gid is None:
                break
            import tempfile
            font_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.ttf', delete=False) as tmp:
                    tmp.write(raw)
                    font_path = Path(tmp.name)
                svg = _single_gid_svg(font_path, gid)
            finally:
                if font_path is not None:
                    try:
                        font_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            pdf_path = out_dir / f'{text}.pdf-gid-{gid}.svg'
            pdf_path.write_text(svg, encoding='utf-8', newline='\n')
            print(f'GENGLYPH PDF MATCH: {text!r} -> GID {gid} ({resource}/{base_font})', flush=True)
            return ref_svg
        except Exception as exc:
            errors.append(f'{resource}/{base_font}: {exc}')

    print(f"GENGLYPH: no confirmed single embedded-PDF GID for {text!r}; reference artwork generated for external visual matching.", flush=True)
    return ref_svg

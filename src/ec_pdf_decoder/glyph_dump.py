"""Dump unresolved PDF glyphs and manually requested text using embedded PDF fonts."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

from .direct_pdf_fixed import embedded_fonts
from .learned_mapping import font_key, load_database


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


def _find_exact_gid(raw: bytes, base_font: str, text: str, mapping_path: Path | None, learned_path: Path | None) -> tuple[int, str]:
    """Resolve text to one exact embedded-font GID; never synthesize a shaped sequence."""
    gid = _learned_text_gid(learned_path, raw, base_font, text)
    if gid is not None:
        return gid, "confirmed learned mapping"
    gid = _mapping_text_gid(mapping_path, base_font, text)
    if gid is not None:
        return gid, "custom mapping"
    gid = _font_exact_text_gid(raw, text)
    if gid is not None:
        return gid, "embedded font cmap/glyph name"
    raise ValueError(
        f"no exact single embedded-font GID is known for {text!r}; "
        "--genglyph refuses to shape it into separate glyphs because that would not be the PDF glyph"
    )


def dump_text_glyph(pdf: bytes, page: int, text: str, out_dir: Path, *, mapping_path: Path | None = Path('custom_glyph_map.json'), learned_path: Path | None = Path('learned_glyph_map.json')) -> Path:
    """Dump the exact embedded PDF glyph mapped to text; do not run HarfBuzz."""
    if not text:
        raise ValueError('genglyph text cannot be empty')
    out_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for resource, base_font, raw in embedded_fonts(pdf, page):
        if not raw:
            continue
        try:
            gid, source = _find_exact_gid(raw, base_font, text, mapping_path, learned_path)
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.ttf', delete=False) as tmp:
                tmp.write(raw)
                font_path = Path(tmp.name)
            try:
                svg = _single_gid_svg(font_path, gid)
            finally:
                try:
                    font_path.unlink(missing_ok=True)
                except OSError:
                    pass
            path = out_dir / f'{text}.svg'
            path.write_text(svg, encoding='utf-8', newline='\n')
            print(f'GENGLYPH: {text!r} -> GID {gid} ({source})', flush=True)
            return path
        except Exception as exc:
            errors.append(f'{resource}/{base_font}: {exc}')
    detail = '; '.join(errors) if errors else 'no embedded FontFile2 resource found on the selected page'
    raise ValueError(f'could not find an exact embedded PDF glyph for {text!r}: {detail}')

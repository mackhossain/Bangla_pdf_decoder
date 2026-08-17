"""Exact visual matching for --genglyph using PDF-derived reference glyphs."""
from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from .direct_pdf_fixed import embedded_fonts
from .glyph_dump import _single_gid_svg

REFERENCE_DB = Path("tools/glyph_lab/reference_glyphs.json")
REFERENCE_DIR = Path("tools/glyph_lab/references")


def _load_reference(text: str) -> dict | None:
    if not REFERENCE_DB.exists():
        return None
    try:
        data = json.loads(REFERENCE_DB.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    glyph = data.get("glyphs", {}).get(text) if isinstance(data, dict) else None
    return glyph if isinstance(glyph, dict) else None


def _reference_svg(text: str) -> tuple[str, dict] | None:
    meta = _load_reference(text)
    if not meta:
        return None

    ref_svg = REFERENCE_DIR / f"{text}.svg"
    if ref_svg.exists():
        try:
            return ref_svg.read_text(encoding="utf-8"), meta
        except OSError:
            pass

    source = Path(str(meta.get("source", "")))
    if not source.exists():
        return None
    page = int(meta.get("page", 1))
    gid = int(meta.get("pdf_cid_gid"))
    raw = source.read_bytes()
    for _resource, _font_name, font_bytes in embedded_fonts(raw, page):
        if not font_bytes:
            continue
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as tmp:
                tmp.write(font_bytes)
                tmp_path = Path(tmp.name)
            return _single_gid_svg(tmp_path, gid), meta
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
    return None


def _svg_score(a: str, b: str) -> float:
    """Return a stable visual score; byte-identical normalized SVG is 100%."""
    if a == b:
        return 100.0
    try:
        import cairosvg
        from PIL import Image, ImageChops

        pa = cairosvg.svg2png(bytestring=a.encode("utf-8"), output_width=512, output_height=512)
        pb = cairosvg.svg2png(bytestring=b.encode("utf-8"), output_width=512, output_height=512)
        ia = Image.open(io.BytesIO(pa)).convert("L")
        ib = Image.open(io.BytesIO(pb)).convert("L")
        if ia.size != ib.size:
            return 0.0
        diff = ImageChops.difference(ia, ib)
        hist = diff.histogram()
        total = sum(i * n for i, n in enumerate(hist))
        denom = ia.width * ia.height * 255
        return max(0.0, 100.0 * (1.0 - total / max(1, denom)))
    except Exception:
        return 0.0


def _font_gid_count(font_bytes: bytes) -> int:
    """Return the actual TrueType glyph count, not the Unicode cmap count."""
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(font_bytes), lazy=False)
    try:
        return len(font.getGlyphOrder())
    finally:
        font.close()


def _candidate_gids(font_bytes: bytes) -> list[int]:
    """Return every drawable GID in the embedded font.

    The previous implementation only considered CIDs that were used on the
    selected page and also unmapped by cmap. That was wrong for --genglyph:
    the gold reference may be a valid glyph in the embedded subset but simply
    not be used on the selected page. The reference for `শ্চ` was GID 340, for
    example, while the page's unresolved-used list did not contain 340.
    """
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(font_bytes), lazy=False)
    try:
        glyph_set = font.getGlyphSet()
        order = font.getGlyphOrder()
        result: list[int] = []
        for gid, name in enumerate(order):
            pen = BoundsPen(glyph_set)
            try:
                glyph_set[name].draw(pen)
            except Exception:
                continue
            if pen.bounds:
                result.append(gid)
        return result
    finally:
        font.close()


def match_reference(pdf: bytes, page: int, text: str, out_dir: Path) -> Path:
    """Match a PDF-derived reference glyph against every drawable embedded-font GID.

    This intentionally does NOT require the target GID to be used by the selected
    page. A reference glyph can be present in the embedded subset but absent from
    that page's content stream. The match is visual and exact: if the candidate SVG
    normalizes byte-for-byte to the stored reference SVG, it is accepted as 100%.
    """
    ref = _reference_svg(text)
    if ref is None:
        raise ValueError(
            f"no PDF-derived reference for {text!r}; create {REFERENCE_DIR / (text + '.svg')} "
            "or add an entry to {REFERENCE_DB}"
        )

    reference_svg, meta = ref
    out_dir.mkdir(parents=True, exist_ok=True)
    scored: list[tuple[float, int, str, str]] = []

    for resource, font_name, font_bytes in embedded_fonts(pdf, page):
        if not font_bytes:
            continue
        candidate_ids = _candidate_gids(font_bytes)
        for gid in candidate_ids:
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as tmp:
                    tmp.write(font_bytes)
                    tmp_path = Path(tmp.name)
                candidate_svg = _single_gid_svg(tmp_path, gid)
            except Exception:
                continue
            finally:
                if tmp_path is not None:
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except OSError:
                        pass

            score = _svg_score(reference_svg, candidate_svg)
            resource_label = str(resource)
            scored.append((score, gid, resource_label, str(font_name)))

            if score >= 100.0:
                path = out_dir / f"{text}.svg"
                path.write_text(candidate_svg, encoding="utf-8", newline="\n")
                meta_path = out_dir / f"{text}.match.json"
                meta_path.write_text(
                    json.dumps(
                        {
                            "text": text,
                            "matched": True,
                            "score": 100.0,
                            "gid": gid,
                            "resource": resource_label,
                            "font": str(font_name),
                            "reference": meta,
                            "candidate_scope": "all drawable GIDs in embedded font",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(f"GENGLYPH EXACT: {text!r} -> GID {gid} (100.000%)")
                print(f"SVG: {path.resolve()}")
                return path

    if not scored:
        raise ValueError(f"no drawable GIDs found in embedded PDF font for {text!r} on page {page}")

    scored.sort(reverse=True)
    print(f"GENGLYPH: no exact 100% match for {text!r}")
    print("TOP CANDIDATES:")
    for score, gid, resource, font_name in scored[:5]:
        print(f"  GID {gid}: {score:.3f}% ({resource}/{font_name})")
    raise ValueError(f"no exact visual match found for {text!r}")

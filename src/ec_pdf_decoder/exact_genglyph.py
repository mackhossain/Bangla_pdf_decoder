"""Exact visual matching for --genglyph using PDF-derived reference glyphs."""
from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from .direct_pdf_fixed import embedded_fonts, ttf_gid_map, used_gids
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


def match_reference(pdf: bytes, page: int, text: str, out_dir: Path) -> Path:
    """Match a PDF-derived reference glyph against unresolved CIDs and dump the winner."""
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
        cmap = ttf_gid_map(font_bytes)
        used = sorted(set(int(cid) for cid in used_gids(pdf, page)))
        unresolved = [cid for cid in used if cid not in cmap]

        for cid in unresolved:
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as tmp:
                    tmp.write(font_bytes)
                    tmp_path = Path(tmp.name)
                candidate_svg = _single_gid_svg(tmp_path, cid)
            finally:
                if tmp_path is not None:
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except OSError:
                        pass

            score = _svg_score(reference_svg, candidate_svg)
            resource_label = str(resource)
            scored.append((score, cid, resource_label, str(font_name)))

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
                            "cid": cid,
                            "resource": resource_label,
                            "font": str(font_name),
                            "reference": meta,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(f"GENGLYPH EXACT: {text!r} -> CID {cid} (100.000%)")
                print(f"SVG: {path.resolve()}")
                return path

    if not scored:
        raise ValueError(f"no unresolved PDF CIDs found for {text!r} on page {page}")

    scored.sort(reverse=True)
    print(f"GENGLYPH: no exact 100% match for {text!r}")
    print("TOP CANDIDATES:")
    for score, cid, resource, font_name in scored[:5]:
        print(f"  CID {cid}: {score:.3f}% ({resource}/{font_name})")
    raise ValueError(f"no exact visual match found for {text!r}")

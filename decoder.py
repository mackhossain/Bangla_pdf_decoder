"""EC Bangla PDF decoder with embedded-TTF fallback and interactive learning."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import unicodedata
from pathlib import Path

from src.ec_pdf_decoder.bangla_harfbuzz import choose_candidate
from src.ec_pdf_decoder.bangla_order import restore_bangla_logical_order_tokens
from src.ec_pdf_decoder.direct_pdf_fixed import (
    analyze_page_direct,
    embedded_fonts,
    ttf_gid_map,
)
from src.ec_pdf_decoder.learned_mapping import font_key, load_database
from src.ec_pdf_decoder.direct_review import run as review_run


def _materialize_mapping(pdf: bytes, page: int, legacy_path: Path, learned_path: Path) -> Path:
    """Build a temporary validated mapping for the direct decoder."""
    base = json.loads(legacy_path.read_text(encoding="utf-8")) if legacy_path.exists() else {}
    learned = load_database(learned_path)
    merged = {k: dict(v) for k, v in base.items()}

    for _resource, base_font, raw in embedded_fonts(pdf, page):
        section = merged.setdefault(base_font, {})
        for gid, text in ttf_gid_map(raw).items():
            section.setdefault(str(gid), text)
        fkey = font_key(raw, base_font)
        glyphs = learned.get("fonts", {}).get(fkey, {}).get("glyphs", {})
        for gid, entry in glyphs.items():
            if isinstance(entry, dict) and entry.get("confidence", 0) >= 1.0:
                section[str(gid)] = str(entry.get("text", ""))

    fd, name = tempfile.mkstemp(prefix="ec_mapping_", suffix=".json")
    os.close(fd)
    path = Path(name)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _flat_mapping(mapping_path: Path) -> dict[str, str]:
    """Flatten the temporary font-scoped mapping for CID/GID reconstruction."""
    try:
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    flat: dict[str, str] = {}
    if not isinstance(data, dict):
        return flat

    for section in data.values():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, str):
                flat[str(key)] = value
            elif isinstance(value, dict) and isinstance(value.get("text"), str):
                flat[str(key)] = value["text"]
    return flat


def _logical_text(report: dict, mapping_path: Path, font_bytes: bytes | None = None) -> str:
    """Rebuild glyph tokens and restore Bengali logical Unicode order.

    The PDF content stream is a visual/shaped glyph stream. We first apply the
    deterministic Bengali reordering pass, then let HarfBuzz shape both the
    original and reordered candidates with the embedded PDF TTF. The candidate
    whose resulting visual GIDs best match the PDF's original CID/GID sequence
    wins. If HarfBuzz is unavailable, the deterministic visual-order recovery
    remains the fallback.
    """
    mapping = _flat_mapping(mapping_path)
    chunks: list[str] = []

    for operation in report.get("operations", []):
        cids = [int(value) for value in operation.get("cids", [])]
        tokens = [mapping.get(str(cid), f"⟦CID:{cid}⟧") for cid in cids]

        original = "".join(tokens)
        reordered = "".join(
            restore_bangla_logical_order_tokens(tokens, visual_order=True)
        )

        if any(token.startswith("⟦CID:") for token in tokens):
            selected = reordered
        else:
            selected, _score = choose_candidate(
                font_bytes, cids, original, reordered
            )
        chunks.append(selected)

    return unicodedata.normalize("NFC", "\n".join(chunks))


def main() -> int:
    parser = argparse.ArgumentParser(description="Decode Bangladesh EC Bangla PDF text")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--mapping", type=Path, default=Path("custom_glyph_map.json"))
    parser.add_argument("--learned", type=Path, default=Path("learned_glyph_map.json"))
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--gid", type=int, help="review only one CID/GID")
    parser.add_argument("--text", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path)
    args = parser.parse_args()

    pdf = args.pdf.read_bytes()
    mapping_path = _materialize_mapping(pdf, args.page, args.mapping, args.learned)
    text = ""
    try:
        report = analyze_page_direct(args.pdf, args.page, mapping_path)

        if args.review and report["missing_cids"]:
            review_run(
                args.pdf,
                args.page,
                args.learned,
                Path("data/bangla_conjuncts.json"),
                args.gid,
            )
            try:
                mapping_path.unlink(missing_ok=True)
            except PermissionError:
                pass
            mapping_path = _materialize_mapping(pdf, args.page, args.mapping, args.learned)
            report = analyze_page_direct(args.pdf, args.page, mapping_path)

        font_bytes = next((raw for _resource, _base, raw in embedded_fonts(pdf, args.page)), None)
        text = _logical_text(report, mapping_path, font_bytes)
    finally:
        try:
            mapping_path.unlink(missing_ok=True)
        except PermissionError:
            pass

    print(f"PDF: {args.pdf.name}")
    print(f"PAGE: {args.page}")
    print(f"MAPPED ENTRIES: {report['tounicode_entries']}")
    print(f"UNRESOLVED CIDs: {report['missing_cids']}")
    print("\n# DECODED TEXT")
    print("=" * 72)
    print(text)

    if args.text:
        args.text.write_text(text + "\n", encoding="utf-8")
    if args.json_path:
        args.json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if not report["missing_cids"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

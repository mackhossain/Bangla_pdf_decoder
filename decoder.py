"""EC Bangla PDF decoder with embedded-TTF fallback and interactive learning."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from src.ec_pdf_decoder.direct_pdf import (
    analyze_page_direct,
    embedded_fonts,
    ttf_gid_map,
)
from src.ec_pdf_decoder.learned_mapping import font_key, load_database
from src.ec_pdf_decoder.direct_review import run as review_run


def _materialize_mapping(pdf: bytes, page: int, legacy_path: Path, learned_path: Path) -> Path:
    """Build a temporary validated mapping for the direct decoder.

    The direct resolver is used here because EC PDFs commonly store
    /Resources as an indirect object.  The old resolver treated the reference
    itself as the dictionary, producing an empty cmap and making even ordinary
    Bengali letters look unresolved.
    """
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
    try:
        report = analyze_page_direct(args.pdf, args.page, mapping_path)

        # Only genuinely unresolved glyphs reach the learner.  Normal Unicode
        # glyphs have already been filled from the embedded TTF cmap.
        if args.review and report["missing_cids"]:
            review_run(
                args.pdf,
                args.page,
                args.learned,
                Path("data/bangla_conjuncts.json"),
                args.gid,
            )
            mapping_path.unlink(missing_ok=True)
            mapping_path = _materialize_mapping(pdf, args.page, args.mapping, args.learned)
            report = analyze_page_direct(args.pdf, args.page, mapping_path)
    finally:
        mapping_path.unlink(missing_ok=True)

    text = "\n".join(str(item["decoded"]) for item in report["operations"])
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

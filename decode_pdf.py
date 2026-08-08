"""Simple production entry point for Bangla PDF decoding.

Examples:
    python decode_pdf.py FILE.pdf --page 3
    python decode_pdf.py FILE.pdf --page 3 --mapping custom_glyph_map.json
    python decode_pdf.py FILE.pdf --page 3 --text page3.txt
    python decode_pdf.py FILE.pdf --page 3 --json page3.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.ec_pdf_decoder.extract import analyze_page


def main() -> int:
    parser = argparse.ArgumentParser(description="Decode Bangladesh EC Identity-H Bangla PDF text")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--page", type=int, help="1-based page number")
    parser.add_argument("--mapping", type=Path, default=Path("custom_glyph_map.json"))
    parser.add_argument("--text", type=Path, help="write decoded text only")
    parser.add_argument("--json", dest="json_path", type=Path, help="write diagnostic JSON")
    args = parser.parse_args()

    if args.page is None:
        raise SystemExit("--page is required for text extraction; use src.ec_pdf_decoder.extract for page inventory")

    report = analyze_page(args.pdf, args.page, args.mapping)
    text = "\n".join(str(item["decoded"]) for item in report["operations"])

    print(f"PDF: {args.pdf.name}")
    print(f"PAGE: {args.page}")
    print(f"MAPPINGS: {report['tounicode_entries']}")
    print(f"UNRESOLVED CIDs: {report['missing_cids']}")
    print("\nDECODED TEXT")
    print("=" * 72)
    print(text)

    if args.text:
        args.text.write_text(text + "\n", encoding="utf-8")
    if args.json_path:
        args.json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if not report["missing_cids"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

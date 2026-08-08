"""Production command for deterministic EC Bangla PDF decoding with learning.

Examples:
    python decoder.py FILE.pdf --page 3
    python decoder.py FILE.pdf --page 3 --review
    python decoder.py FILE.pdf --page 3 --review --text page3.txt

The command first uses the validated legacy mapping, then loads mappings learned
for the exact embedded font fingerprint.  With --review, unresolved custom
GIDs are sent to the interactive learner.  Confirmed mappings are saved at
confidence 1.0 and the page is decoded again automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from src.ec_pdf_decoder.extract import (
    _decode_stream, _dict_value, _font_descriptor_info, _font_refs,
    _object_map, _page_objects, _resources_value, analyze_page,
)
from src.ec_pdf_decoder.learned_mapping import font_key, load_database
from review_mappings import run as review_run


def _page_font_keys(pdf: bytes, page: int):
    objects = _object_map(pdf)
    pages = _page_objects(objects)
    if page < 1 or page > len(pages):
        raise ValueError(f"page {page} outside 1..{len(pages)}")
    page_body = pages[page - 1][1]
    resources = _resources_value(page_body, b"Resources")
    for _resource, ref in _font_refs(resources).items():
        info = _font_descriptor_info(objects, ref)
        descriptor_ref = info.get("font_descriptor")
        descriptor = objects.get(descriptor_ref, b"") if isinstance(descriptor_ref, int) else b""
        ff = _dict_value(descriptor, b"FontFile2")
        refs = re.findall(rb"(\d+)\s+\d+\s+R", ff or b"")
        if not refs:
            continue
        font_bytes = _decode_stream(objects.get(int(refs[0]), b""))
        if font_bytes:
            yield str(info.get("base_font", "")), font_key(font_bytes, str(info.get("base_font", "")))


def _materialize_mapping(pdf: bytes, page: int, legacy_path: Path, learned_path: Path) -> Path:
    base = json.loads(legacy_path.read_text(encoding="utf-8")) if legacy_path.exists() else {}
    learned = load_database(learned_path)
    merged = {key: dict(value) for key, value in base.items()}
    for base_font, fkey in _page_font_keys(pdf, page):
        section = merged.setdefault(base_font, {})
        learned_font = learned.get("fonts", {}).get(fkey, {})
        for gid, entry in learned_font.get("glyphs", {}).items():
            if isinstance(entry, dict) and entry.get("confidence", 0) >= 1.0:
                section[str(gid)] = str(entry["text"])

    # tempfile.mkstemp() leaves an OS-level file descriptor open. On Windows
    # that descriptor keeps the file locked, so the later unlink() in main()
    # fails with WinError 32. Close the descriptor immediately before writing.
    fd, filename = tempfile.mkstemp(prefix="ec_mapping_", suffix=".json")
    os.close(fd)
    temp = Path(filename)
    temp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return temp


def main() -> int:
    parser = argparse.ArgumentParser(description="Decode Bangladesh EC Bangla PDF text")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--mapping", type=Path, default=Path("custom_glyph_map.json"))
    parser.add_argument("--learned", type=Path, default=Path("learned_glyph_map.json"))
    parser.add_argument("--review", action="store_true", help="interactively learn unresolved custom glyphs")
    parser.add_argument("--text", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path)
    args = parser.parse_args()

    pdf = args.pdf.read_bytes()
    mapping_path = _materialize_mapping(pdf, args.page, args.mapping, args.learned)
    try:
        report = analyze_page(args.pdf, args.page, mapping_path)
        if args.review and report["missing_cids"]:
            review_run(args.pdf, args.page, args.learned, Path("data/bangla_conjuncts.json"), None)
            mapping_path.unlink(missing_ok=True)
            mapping_path = _materialize_mapping(pdf, args.page, args.mapping, args.learned)
            report = analyze_page(args.pdf, args.page, mapping_path)
    finally:
        mapping_path.unlink(missing_ok=True)

    text = "\n".join(str(item["decoded"]) for item in report["operations"])
    print(f"PDF: {args.pdf.name}")
    print(f"PAGE: {args.page}")
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

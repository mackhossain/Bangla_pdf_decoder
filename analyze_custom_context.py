"""Show context around unresolved Identity-H CIDs.

Usage:
    python analyze_custom_context.py FILE.pdf 3 --mapping custom_glyph_map.json

PDF content is processed as bytes. No regex is applied to decoded text.
Validated custom mappings are applied before unresolved CIDs are reported.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.ec_pdf_decoder.extract import (
    _decode_identity_h, _decode_stream, _font_descriptor_info, _font_refs,
    _object_map, _page_objects, _resources_value, _page_operations,
    parse_tounicode,
)


def load_mapping(path: Path | None) -> dict[str, dict[int, str]]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(font): {int(cid): str(text) for cid, text in values.items()}
            for font, values in data.items() if isinstance(values, dict)}


def main(pdf_name: str, page_number: int, mapping_path: Path | None = None) -> int:
    pdf_path = Path(pdf_name)
    objects = _object_map(pdf_path.read_bytes())
    pages = _page_objects(objects)
    if not 1 <= page_number <= len(pages):
        raise SystemExit(f"page {page_number} outside 1..{len(pages)}")

    page_object, page_body = pages[page_number - 1]
    fonts = _font_refs(_resources_value(page_body, b"Resources"))
    overrides = load_mapping(mapping_path)
    cmap: dict[int, str] = {}
    font_names: list[str] = []

    print("PDF CID CUSTOM CONTEXT")
    print("=" * 72)
    print(f"PDF: {pdf_path.name}")
    print(f"PAGE: {page_number}")
    print(f"PAGE OBJECT: {page_object} 0 R")

    for resource_name, font_ref in fonts.items():
        info = _font_descriptor_info(objects, font_ref)
        base_font = str(info.get("base_font", ""))
        font_names.append(base_font)
        print(f"FONT {resource_name.decode('latin1')}: {base_font}")
        cmap_ref = info.get("tounicode")
        if isinstance(cmap_ref, int):
            stream = _decode_stream(objects.get(cmap_ref, b""))
            if stream:
                cmap.update(parse_tounicode(stream))
        cmap.update(overrides.get(base_font, {}))

    operations = []
    for content_num, decoded, shows in _page_operations(objects, page_body):
        print(f"CONTENT {content_num} 0 R: {len(decoded)} bytes, {len(shows)} text operations")
        operations.extend((content_num, show) for show in shows)

    all_cids: list[int] = []
    for _content_num, show in operations:
        values = show.value if show.operator == b"TJ" else (show.value,)
        for value in values:
            if isinstance(value, bytes):
                all_cids.extend(_decode_identity_h(value))

    unresolved = sorted({cid for cid in all_cids if cid not in cmap})
    print("\nMAPPING SUMMARY")
    print("-" * 72)
    print(f"Fonts: {font_names}")
    print(f"ToUnicode/custom mappings: {len(cmap)}")
    print(f"Used CIDs: {len(set(all_cids))}")
    print(f"Unresolved CIDs: {unresolved}")

    if not unresolved:
        print("\nNo unresolved CIDs remain on this page.")
        return 0

    print("\nCID OCCURRENCES")
    print("-" * 72)
    for target in unresolved:
        print(f"\nTARGET CID/GID: {target}")
        occurrence = 0
        for content_num, show in operations:
            values = show.value if show.operator == b"TJ" else (show.value,)
            for value in values:
                if not isinstance(value, bytes):
                    continue
                cids = _decode_identity_h(value)
                for index, cid in enumerate(cids):
                    if cid != target:
                        continue
                    occurrence += 1
                    lo, hi = max(0, index - 8), min(len(cids), index + 9)
                    context = cids[lo:hi]
                    context_text = " ".join(cmap.get(c, f"⟦{c}⟧") for c in context)
                    raw = value[lo * 2:hi * 2].hex().upper()
                    print(f"  occurrence {occurrence}: content={content_num} op={show.operator.decode('latin1')}")
                    print(f"    CID/GID context: {context}")
                    print(f"    Unicode context: {context_text}")
                    print(f"    raw hex: {raw}")

    print("\nNEXT STEP")
    print("-" * 72)
    print("Only the unresolved CIDs above need evidence-based reconstruction.")
    print("Do not add outline-similarity guesses to the production mapping.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze unresolved PDF CIDs safely")
    parser.add_argument("pdf")
    parser.add_argument("page", type=int)
    parser.add_argument("--mapping", type=Path, default=Path("custom_glyph_map.json"))
    args = parser.parse_args()
    raise SystemExit(main(args.pdf, args.page, args.mapping))

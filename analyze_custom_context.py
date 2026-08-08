"""Show the byte/CID context around unresolved Identity-H glyphs.

Usage:
    python analyze_custom_context.py FILE.pdf 3

Unlike the previous regex-based experiment, this script never applies a text
regex to a bytes object. PDF content is intentionally processed as bytes and
CIDs are decoded only after the Identity-H boundary is known.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from src.ec_pdf_decoder.extract import _decode_identity_h, _decode_stream, _object_map, _page_objects, _contents_refs, _resources_value, _font_refs, _font_descriptor_info, parse_tounicode, _page_operations


def main(pdf_name: str, page_number: int) -> int:
    pdf_path = Path(pdf_name)
    pdf = pdf_path.read_bytes()
    objects = _object_map(pdf)
    pages = _page_objects(objects)
    if not 1 <= page_number <= len(pages):
        raise SystemExit(f"page {page_number} outside 1..{len(pages)}")

    page_object, page_body = pages[page_number - 1]
    resources = _resources_value(page_body, b"Resources")
    fonts = _font_refs(resources)
    cmap: dict[int, str] = {}

    print("PDF CID CUSTOM CONTEXT")
    print("=" * 72)
    print(f"PDF: {pdf_path.name}")
    print(f"PAGE: {page_number}")
    print(f"PAGE OBJECT: {page_object} 0 R")

    for resource_name, font_ref in fonts.items():
        info = _font_descriptor_info(objects, font_ref)
        print(f"FONT {resource_name.decode('latin1')}: {info}")
        cmap_ref = info.get("tounicode")
        if isinstance(cmap_ref, int):
            stream = _decode_stream(objects.get(cmap_ref, b""))
            if stream:
                cmap.update(parse_tounicode(stream))

    operations = []
    for content_num, decoded, shows in _page_operations(objects, page_body):
        print(f"CONTENT {content_num} 0 R: {len(decoded)} bytes, {len(shows)} text operations")
        operations.extend((content_num, decoded, show) for show in shows)

    all_cids: list[int] = []
    for content_num, _decoded, show in operations:
        values = show.value if show.operator == b"TJ" else (show.value,)
        for value in values:
            if isinstance(value, bytes):
                all_cids.extend(_decode_identity_h(value))

    unresolved = sorted({cid for cid in all_cids if cid not in cmap})
    print("\nTOUNICODE SUMMARY")
    print("-" * 72)
    print(f"Mapped CIDs: {len(cmap)}")
    print(f"Used CIDs: {len(set(all_cids))}")
    print(f"Unresolved CIDs: {unresolved}")

    print("\nCID OCCURRENCES")
    print("-" * 72)
    for target in unresolved:
        print(f"\nTARGET CID/GID: {target}")
        occurrence = 0
        for content_num, decoded, show in operations:
            values = show.value if show.operator == b"TJ" else (show.value,)
            for value in values:
                if not isinstance(value, bytes):
                    continue
                cids = _decode_identity_h(value)
                for index, cid in enumerate(cids):
                    if cid != target:
                        continue
                    occurrence += 1
                    lo = max(0, index - 6)
                    hi = min(len(cids), index + 7)
                    context = cids[lo:hi]
                    context_text = " ".join(cmap.get(c, f"⟦{c}⟧") for c in context)
                    raw_start = lo * 2
                    raw_end = hi * 2
                    raw = value[raw_start:raw_end].hex().upper()
                    print(f"  occurrence {occurrence}: content={content_num} op={show.operator.decode('latin1')}")
                    print(f"    CID context: {context}")
                    print(f"    Unicode/context: {context_text}")
                    print(f"    raw hex: {raw}")

    print("\nNEXT STEP")
    print("-" * 72)
    if unresolved:
        print("Only the unresolved CIDs above require custom-glyph reconstruction.")
        print("Do not infer them from outline similarity alone; compare their PDF")
        print("contexts and the rendered/reference text, then add a validated mapping.")
    else:
        print("No unresolved CIDs remain on this page. ToUnicode fully decodes it.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze context around unresolved PDF CIDs")
    parser.add_argument("pdf")
    parser.add_argument("page", type=int)
    args = parser.parse_args()
    raise SystemExit(main(args.pdf, args.page))

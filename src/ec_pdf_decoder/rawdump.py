"""Dump decoded PDF page content streams as hex and ASCII.

This diagnostic tool intentionally does not parse PDF text operators. It shows the
exact decoded bytes emitted by a page content stream so parser behavior can be
verified against the real PDF syntax.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .extract import _decode_stream, _object_map, _contents_refs, _page_objects


def _ascii_preview(chunk: bytes) -> str:
    return "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)


def _dump(data: bytes, start: int = 0, length: int | None = None, width: int = 16) -> None:
    end = len(data) if length is None else min(len(data), start + max(0, length))
    if start < 0 or start > len(data):
        raise ValueError("start offset is outside the stream")
    if width not in (8, 16, 32):
        raise ValueError("width must be 8, 16, or 32")

    for offset in range(start, end, width):
        chunk = data[offset:min(offset + width, end)]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        hex_part = f"{hex_part:<{width * 3 - 1}}"
        print(f"{offset:08X}  {hex_part}  |{_ascii_preview(chunk)}|")


def dump_page(pdf_path: Path, page_number: int, max_bytes: int | None, start: int) -> int:
    pdf = pdf_path.read_bytes()
    objects = _object_map(pdf)
    pages = _page_objects(objects)

    if page_number < 1 or page_number > len(pages):
        raise ValueError(f"page must be between 1 and {len(pages)}")

    page_object, page_body = pages[page_number - 1]
    refs = _contents_refs(page_body)

    print(f"PDF: {pdf_path}")
    print(f"PAGE: {page_number}")
    print(f"PAGE OBJECT: {page_object} 0 R")
    print(f"CONTENTS OBJECTS: {', '.join(f'{x} 0 R' for x in refs) or '<none>'}")

    if not refs:
        print("No /Contents stream on this page.")
        return 0

    for content_object in refs:
        body = objects.get(content_object)
        if body is None:
            print(f"\n=== CONTENTS {content_object} 0 R: MISSING ===")
            continue
        decoded = _decode_stream(body)
        if decoded is None:
            print(f"\n=== CONTENTS {content_object} 0 R: NO STREAM ===")
            continue

        print(f"\n=== CONTENTS {content_object} 0 R ===")
        print(f"Decoded length: {len(decoded)} bytes")
        print(f"Dump range: {start}..{min(len(decoded), start + max_bytes) if max_bytes is not None else len(decoded)}")
        print("Offset    Hex bytes                                         ASCII")
        print("--------  ------------------------------------------------  ----------------")
        _dump(decoded, start=start, length=max_bytes)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dump a decoded PDF page content stream as hex and ASCII."
    )
    parser.add_argument("pdf", type=Path, help="PDF file")
    parser.add_argument("--page", type=int, default=3, help="1-based page number (default: 3)")
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=None,
        help="maximum number of bytes to dump; default is the whole stream",
    )
    parser.add_argument("--start", type=int, default=0, help="starting byte offset")
    args = parser.parse_args()

    if args.max_bytes is not None and args.max_bytes < 0:
        parser.error("--max-bytes must be non-negative")
    if args.start < 0:
        parser.error("--start must be non-negative")

    try:
        return dump_page(args.pdf, args.page, args.max_bytes, args.start)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

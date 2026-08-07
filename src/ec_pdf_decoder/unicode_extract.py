"""End-to-end Unicode extraction from EC PDF text streams using ToUnicode."""

from __future__ import annotations

import argparse
from pathlib import Path

from .extract import (
    _contents_refs,
    _decode_stream,
    _fallback_text_operations,
    _object_map,
    _page_objects,
)
from .integration import inspect_tounicode
from .textdecode import decode_text_shows


def extract_unicode_pages(path: str | Path, font_object: int = 6) -> list[str]:
    """Return one Unicode string per PDF page using the embedded ToUnicode CMap."""
    pdf_path = Path(path)
    pdf = pdf_path.read_bytes()
    objects = _object_map(pdf)
    pages = _page_objects(objects)
    cmap = inspect_tounicode(str(pdf_path), font_object).cmap

    page_text: list[str] = []
    for _page_number, page_body in pages:
        pieces: list[str] = []
        for content_number in _contents_refs(page_body):
            body = objects.get(content_number)
            if body is None:
                continue
            decoded = _decode_stream(body)
            if decoded is None:
                continue
            operations = _fallback_text_operations(decoded)
            if not operations:
                continue
            pieces.extend(decode_text_shows(operations, cmap))
        page_text.append("".join(pieces))
    return page_text


def extract_unicode(path: str | Path, font_object: int = 6) -> int:
    """Print page-by-page Unicode text decoded from the PDF's embedded CMap."""
    pdf_path = Path(path)
    pages = extract_unicode_pages(pdf_path, font_object)
    print(f"PDF: {pdf_path}")
    print(f"Pages: {len(pages)}")
    for number, text in enumerate(pages, 1):
        print(f"\n=== PAGE {number} ===")
        print(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract PDF text through the embedded ToUnicode CMap; no OCR"
    )
    parser.add_argument("pdf", type=Path, help="PDF file to decode")
    parser.add_argument(
        "--font-object",
        type=int,
        default=6,
        help="Type0 font object containing /ToUnicode (default: 6)",
    )
    args = parser.parse_args()
    return extract_unicode(args.pdf, args.font_object)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["extract_unicode", "extract_unicode_pages"]

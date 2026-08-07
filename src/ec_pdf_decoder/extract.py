"""Extract raw text-showing operations from a real PDF without OCR."""

from __future__ import annotations

import argparse
import re
import zlib
from pathlib import Path

from .content import TextShow, extract_text_show_operations


def _objects(pdf: bytes):
    pattern = re.compile(rb"(?ms)(\d+)\s+(\d+)\s+obj\s*(.*?)\s*endobj")
    for match in pattern.finditer(pdf):
        yield int(match.group(1)), int(match.group(2)), match.group(3)


def _object_map(pdf: bytes) -> dict[int, bytes]:
    return {num: body for num, _gen, body in _objects(pdf)}


def _ref(body: bytes, key: bytes) -> int | None:
    m = re.search(rb"/" + re.escape(key) + rb"\s+(\d+)\s+\d+\s+R", body)
    return int(m.group(1)) if m else None


def _dict_value(body: bytes, key: bytes) -> bytes | None:
    m = re.search(rb"/" + re.escape(key) + rb"\s+(\[.*?\]|<<.*?>>|\d+\s+\d+\s+R)", body, re.S)
    return m.group(1) if m else None


def _stream(body: bytes) -> bytes | None:
    marker = re.search(rb"\bstream\r?\n", body)
    if not marker:
        return None
    start = marker.end()
    end = body.find(b"endstream", start)
    if end < 0:
        return None
    return body[start:end].rstrip(b"\r\n")


def _decode_stream(body: bytes) -> bytes | None:
    stream = _stream(body)
    if stream is None:
        return None
    filters = _dict_value(body, b"Filter")
    if filters and b"FlateDecode" in filters:
        return zlib.decompress(stream)
    return stream


def _page_objects(objects: dict[int, bytes]):
    return sorted((num, body) for num, body in objects.items() if re.search(rb"/Type\s*/Page(?:\s|/|>)", body))


def _contents_refs(body: bytes) -> list[int]:
    value = _dict_value(body, b"Contents")
    if not value:
        return []
    return [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", value)]


def _format_value(value: object) -> str:
    if isinstance(value, bytes):
        return value.hex(" ") or "<empty>"
    return repr(value)


def extract_pdf(path: str | Path) -> int:
    pdf_path = Path(path)
    pdf = pdf_path.read_bytes()
    objects = _object_map(pdf)
    pages = _page_objects(objects)

    print(f"PDF: {pdf_path}")
    print(f"Objects: {len(objects)}")
    print(f"Pages: {len(pages)}")

    total = 0
    for page_index, (page_num, page_body) in enumerate(pages, 1):
        print(f"\n=== PAGE {page_index} (object {page_num} 0 R) ===")
        refs = _contents_refs(page_body)
        if not refs:
            print("No /Contents")
            continue

        for content_num in refs:
            body = objects.get(content_num)
            if body is None:
                print(f"Contents {content_num} 0 R: missing object")
                continue
            decoded = _decode_stream(body)
            if decoded is None:
                print(f"Contents {content_num} 0 R: no stream")
                continue
            print(f"-- Contents {content_num} 0 R: {len(decoded)} decoded bytes")
            try:
                operations = extract_text_show_operations(decoded)
            except Exception as exc:
                print(f"Content parse error: {exc}")
                continue

            for index, operation in enumerate(operations, 1):
                total += 1
                if isinstance(operation, TextShow):
                    print(f"[{index:04d}] {operation.operator.decode('latin1')}: {operation.value!r}")
                    if operation.operator == b"TJ":
                        for part in operation.value:
                            print(f"       {_format_value(part)}")
                    else:
                        print(f"       raw: {_format_value(operation.value)}")

    print(f"\nText-show operations: {total}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect real PDF text-showing operations without OCR")
    parser.add_argument("pdf", type=Path, help="PDF file to inspect")
    args = parser.parse_args()
    return extract_pdf(args.pdf)


if __name__ == "__main__":
    raise SystemExit(main())

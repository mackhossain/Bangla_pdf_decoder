"""Extract raw text-showing operations from real PDFs without OCR."""

from __future__ import annotations

import argparse
import re
import zlib
from pathlib import Path

from .content import TextShow, extract_text_show_operations


_OBJECT_RE = re.compile(rb"(?ms)(\d+)\s+(\d+)\s+obj\s*(.*?)\s*endobj")


def _objects(pdf: bytes):
    for match in _OBJECT_RE.finditer(pdf):
        yield int(match.group(1)), int(match.group(2)), match.group(3)


def _object_map(pdf: bytes) -> dict[int, bytes]:
    return {num: body for num, _gen, body in _objects(pdf)}


def _dict_value(body: bytes, key: bytes) -> bytes | None:
    """Return a simple PDF dictionary value following *key*.

    PDF token boundaries do not require whitespace between adjacent names.
    For example, ``<</Filter/FlateDecode/Length 2777>>`` is valid syntax.
    The separator before a value may therefore be whitespace *or* the leading
    slash of another PDF name.  This helper accepts both compact and spaced
    forms while retaining support for arrays, dictionaries, and references.
    """
    m = re.search(
        rb"/" + re.escape(key) + rb"(?:\s*)?("
        rb"\[.*?\]"
        rb"|<<.*?>>"
        rb"|\d+\s+\d+\s+R"
        rb"|/[A-Za-z0-9_.+#-]+"
        rb"|[+-]?\d+(?:\.\d+)?"
        rb")",
        body,
        re.S,
    )
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
        try:
            return zlib.decompress(stream)
        except zlib.error as exc:
            raise ValueError(f"FlateDecode failed: {exc}") from exc
    return stream


def _page_objects(objects: dict[int, bytes]):
    return sorted(
        (num, body)
        for num, body in objects.items()
        if re.search(rb"/Type\s*/Page(?:\s|/|>)", body)
    )


def _contents_refs(body: bytes) -> list[int]:
    value = _dict_value(body, b"Contents")
    if not value:
        return []
    return [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", value)]


def _format_bytes(value: bytes) -> str:
    return value.hex(" ") or "<empty>"


def _scan_literal(data: bytes, start: int) -> tuple[bytes, int] | None:
    if start >= len(data) or data[start] != ord("("):
        return None
    out = bytearray()
    depth = 1
    i = start + 1
    while i < len(data):
        b = data[i]
        if b == ord("\\"):
            if i + 1 >= len(data):
                return None
            esc = data[i + 1]
            if esc in b"nrtbf":
                out.append({ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}[esc])
            elif esc in b"\\()":
                out.append(esc)
            elif 48 <= esc <= 55:
                digits = bytearray([esc])
                j = i + 2
                while len(digits) < 3 and j < len(data) and 48 <= data[j] <= 55:
                    digits.append(data[j])
                    j += 1
                out.append(int(bytes(digits), 8) & 0xFF)
                i = j
                continue
            else:
                out.append(esc)
            i += 2
            continue
        if b == ord("("):
            depth += 1
            out.append(b)
        elif b == ord(")"):
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
            out.append(b)
        else:
            out.append(b)
        i += 1
    return None


def _scan_hex(data: bytes, start: int) -> tuple[bytes, int] | None:
    if start >= len(data) or data[start] != ord("<") or (start + 1 < len(data) and data[start + 1] == ord("<")):
        return None
    end = data.find(b">", start + 1)
    if end < 0:
        return None
    raw = re.sub(rb"\s+", b"", data[start + 1:end])
    if len(raw) % 2:
        raw += b"0"
    try:
        return bytes.fromhex(raw.decode("ascii")), end + 1
    except (ValueError, UnicodeDecodeError):
        return None


def _skip_space(data: bytes, pos: int) -> int:
    while pos < len(data) and data[pos] in b"\x00\t\n\f\r ":
        pos += 1
    return pos


def _literal_or_hex_followed_by(data: bytes, start: int, operator: bytes) -> tuple[bytes, int] | None:
    if data[start:start + 1] == b"(":
        parsed = _scan_literal(data, start)
    elif data[start:start + 1] == b"<":
        parsed = _scan_hex(data, start)
    else:
        parsed = None
    if parsed is None:
        return None
    value, pos = parsed
    pos = _skip_space(data, pos)
    if data[pos:pos + len(operator)] != operator:
        return None
    end = pos + len(operator)
    if end < len(data) and data[end] not in b"\x00\t\n\f\r []()<>/%":
        return None
    return value, end


def _fallback_text_operations(data: bytes) -> list[TextShow]:
    """Recover Tj/TJ operations even when an unrelated stream token is malformed."""
    found: list[tuple[int, TextShow]] = []
    n = len(data)

    for i in range(n):
        if data[i:i + 1] not in (b"(", b"<"):
            continue
        for op in (b"Tj", b"'", b'"'):
            parsed = _literal_or_hex_followed_by(data, i, op)
            if parsed is not None:
                value, end = parsed
                found.append((i, TextShow(op, value)))
                break

    i = 0
    while i < n:
        if data[i:i + 1] != b"[":
            i += 1
            continue
        j = i + 1
        values: list[bytes | int | float] = []
        valid = True
        while j < n:
            j = _skip_space(data, j)
            if j >= n:
                valid = False
                break
            if data[j:j + 1] == b"]":
                j = _skip_space(data, j + 1)
                if data[j:j + 2] != b"TJ":
                    valid = False
                else:
                    j += 2
                break
            if data[j:j + 1] == b"(":
                parsed = _scan_literal(data, j)
                if parsed is None:
                    valid = False
                    break
                value, j = parsed
                values.append(value)
                continue
            if data[j:j + 1] == b"<":
                parsed = _scan_hex(data, j)
                if parsed is None:
                    valid = False
                    break
                value, j = parsed
                values.append(value)
                continue
            m = re.match(rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", data[j:])
            if m:
                token = m.group(0)
                values.append(float(token) if any(c in token for c in b".eE") else int(token))
                j += len(token)
                continue
            valid = False
            break
        if valid:
            found.append((i, TextShow(b"TJ", tuple(values))))
            i = j
        else:
            i += 1

    found.sort(key=lambda item: item[0])
    return [operation for _, operation in found]


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
            try:
                decoded = _decode_stream(body)
            except ValueError as exc:
                print(f"Contents {content_num} 0 R: {exc}")
                continue
            if decoded is None:
                print(f"Contents {content_num} 0 R: no stream")
                continue

            print(f"-- Contents {content_num} 0 R: {len(decoded)} decoded bytes")
            operations: list[TextShow] = []
            try:
                operations = extract_text_show_operations(decoded)
            except Exception as exc:
                print(f"Strict parser: {exc}")

            fallback = _fallback_text_operations(decoded)
            if fallback:
                operations = fallback
                print(f"Recovered text operations: {len(operations)}")
            elif operations:
                print(f"Text operations: {len(operations)}")
            else:
                print("No text-showing operations recovered")

            for index, operation in enumerate(operations, 1):
                total += 1
                print(f"[{index:04d}] {operation.operator.decode('latin1')}: {operation.value!r}")
                if operation.operator == b"TJ":
                    for part in operation.value:
                        if isinstance(part, bytes):
                            print(f"       bytes: {_format_bytes(part)}")
                        else:
                            print(f"       adjustment: {part}")
                else:
                    print(f"       raw: {_format_bytes(operation.value)}")

    print(f"\nText-show operations: {total}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect raw PDF text-showing operations without OCR")
    parser.add_argument("pdf", type=Path, help="PDF file to inspect")
    args = parser.parse_args()
    return extract_pdf(args.pdf)


if __name__ == "__main__":
    raise SystemExit(main())

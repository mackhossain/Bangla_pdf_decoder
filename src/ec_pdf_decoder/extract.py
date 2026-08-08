"""Deterministic PDF text extraction with CID, ToUnicode and embedded-font diagnostics.

This module deliberately keeps PDF text as bytes until the PDF font mapping layer
has enough information to interpret it. It does not OCR and does not guess a
Unicode character from a glyph outline.
"""

from __future__ import annotations

import argparse
import json
import re
import zlib
from pathlib import Path
from typing import Iterable

from .content import TextShow, extract_text_show_operations

_OBJECT_RE = re.compile(rb"(?ms)(\d+)\s+(\d+)\s+obj\s*(.*?)\s*endobj")
_HEX = re.compile(rb"<([0-9A-Fa-f]+)>")
_WHITESPACE = b"\x00\t\n\f\r "


def _objects(pdf: bytes):
    for match in _OBJECT_RE.finditer(pdf):
        yield int(match.group(1)), int(match.group(2)), match.group(3)


def _object_map(pdf: bytes) -> dict[int, bytes]:
    return {num: body for num, _gen, body in _objects(pdf)}


def _dict_value(body: bytes, key: bytes) -> bytes | None:
    m = re.search(rb"/" + re.escape(key) + rb"(?:\s*)?(\[.*?\]|<<.*?>>|\d+\s+\d+\s+R|/[A-Za-z0-9_.+#-]+|[+-]?\d+(?:\.\d+)?)", body, re.S)
    return m.group(1) if m else None


def _stream(body: bytes) -> bytes | None:
    marker = re.search(rb"\bstream(?:\r\n|\n|\r)", body)
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
    return sorted((num, body) for num, body in objects.items() if re.search(rb"/Type\s*/Page(?:\s|/|>)", body))


def _contents_refs(body: bytes) -> list[int]:
    value = _dict_value(body, b"Contents")
    return [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", value or b"")]


def _resources_value(body: bytes, key: bytes) -> bytes | None:
    resources = _dict_value(body, b"Resources")
    return _dict_value(resources, key) if resources else None


def _font_refs(resources: bytes | None) -> dict[bytes, int]:
    if not resources:
        return {}
    font_dict = _dict_value(resources, b"Font")
    if not font_dict:
        return {}
    return {name: int(ref) for name, ref in re.findall(rb"/(\S+)\s+(\d+)\s+\d+\s+R", font_dict)}


def _scan_literal(data: bytes, start: int) -> tuple[bytes, int] | None:
    if start >= len(data) or data[start] != ord("("):
        return None
    out = bytearray(); depth = 1; i = start + 1
    while i < len(data):
        b = data[i]
        if b == ord("\\"):
            if i + 1 >= len(data): return None
            esc = data[i + 1]
            if esc in b"nrtbf": out.append({ord("n"):10,ord("r"):13,ord("t"):9,ord("b"):8,ord("f"):12}[esc])
            elif esc in b"\\()": out.append(esc)
            elif 48 <= esc <= 55:
                digits = bytearray([esc]); j = i + 2
                while len(digits) < 3 and j < len(data) and 48 <= data[j] <= 55:
                    digits.append(data[j]); j += 1
                out.append(int(bytes(digits), 8) & 0xFF); i = j; continue
            else: out.append(esc)
            i += 2; continue
        if b == ord("("): depth += 1; out.append(b)
        elif b == ord(")"):
            depth -= 1
            if depth == 0: return bytes(out), i + 1
            out.append(b)
        else: out.append(b)
        i += 1
    return None


def _scan_hex(data: bytes, start: int) -> tuple[bytes, int] | None:
    if start >= len(data) or data[start] != ord("<") or (start + 1 < len(data) and data[start + 1] == ord("<")):
        return None
    end = data.find(b">", start + 1)
    if end < 0: return None
    raw = re.sub(rb"\s+", b"", data[start + 1:end])
    if len(raw) % 2: raw += b"0"
    try: return bytes.fromhex(raw.decode("ascii")), end + 1
    except (ValueError, UnicodeDecodeError): return None


def _skip_space(data: bytes, pos: int) -> int:
    while pos < len(data) and data[pos] in _WHITESPACE: pos += 1
    return pos


def _literal_or_hex_followed_by(data: bytes, start: int, operator: bytes) -> tuple[bytes, int] | None:
    parsed = _scan_literal(data, start) if data[start:start + 1] == b"(" else _scan_hex(data, start)
    if parsed is None: return None
    value, pos = parsed; pos = _skip_space(data, pos)
    if data[pos:pos + len(operator)] != operator: return None
    end = pos + len(operator)
    if end < len(data) and data[end] not in b"\x00\t\n\f\r []()<>/%": return None
    return value, end


def _fallback_text_operations(data: bytes) -> list[TextShow]:
    found: list[tuple[int, TextShow]] = []; n = len(data)
    for i in range(n):
        if data[i:i + 1] not in (b"(", b"<"): continue
        for op in (b"Tj", b"'", b'"'):
            parsed = _literal_or_hex_followed_by(data, i, op)
            if parsed is not None:
                found.append((i, TextShow(op, parsed[0]))); break
    i = 0
    while i < n:
        if data[i:i + 1] != b"[": i += 1; continue
        j = i + 1; values: list[bytes | int | float] = []; valid = True
        while j < n:
            j = _skip_space(data, j)
            if j >= n: valid = False; break
            if data[j:j + 1] == b"]":
                j = _skip_space(data, j + 1)
                if data[j:j + 2] != b"TJ": valid = False
                else: j += 2
                break
            if data[j:j + 1] == b"(":
                parsed = _scan_literal(data, j)
                if parsed is None: valid = False; break
                value, j = parsed; values.append(value); continue
            if data[j:j + 1] == b"<":
                parsed = _scan_hex(data, j)
                if parsed is None: valid = False; break
                value, j = parsed; values.append(value); continue
            m = re.match(rb"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", data[j:])
            if m:
                token = m.group(0); values.append(float(token) if any(c in token for c in b".eE") else int(token)); j += len(token); continue
            valid = False; break
        if valid: found.append((i, TextShow(b"TJ", tuple(values)))); i = j
        else: i += 1
    found.sort(key=lambda item: item[0]); return [operation for _, operation in found]


def _hex_bytes(token: bytes) -> bytes:
    raw = token.strip()[1:-1]
    if len(raw) % 2: raw += b"0"
    return bytes.fromhex(raw.decode("ascii"))


def _cid(token: bytes) -> int:
    return int.from_bytes(_hex_bytes(token), "big")


def _unicode(token: bytes) -> str:
    raw = _hex_bytes(token)
    if len(raw) % 2: raw += b"\x00"
    return raw.decode("utf-16-be", errors="strict")


def _increment_utf16be(value: bytes, amount: int) -> bytes:
    return (int.from_bytes(value, "big") + amount).to_bytes(len(value), "big")


def parse_tounicode(data: bytes) -> dict[int, str]:
    result: dict[int, str] = {}
    for block in re.finditer(rb"beginbfchar\s*(.*?)\s*endbfchar", data, re.S):
        for line in block.group(1).splitlines():
            tokens = _HEX.findall(line)
            if len(tokens) >= 2: result[_cid(b"<" + tokens[0] + b">")] = _unicode(b"<" + tokens[1] + b">")
    for block in re.finditer(rb"beginbfrange\s*(.*?)\s*endbfrange", data, re.S):
        for line in block.group(1).splitlines():
            tokens = _HEX.findall(line)
            if len(tokens) < 3: continue
            start = _cid(b"<" + tokens[0] + b">"); end = _cid(b"<" + tokens[1] + b">")
            if start > end: continue
            if len(tokens) == 3:
                base = _hex_bytes(b"<" + tokens[2] + b">")
                for offset, cid in enumerate(range(start, end + 1)):
                    result[cid] = _increment_utf16be(base, offset).decode("utf-16-be", errors="strict")
            else:
                for offset, cid in enumerate(range(start, end + 1)):
                    if offset + 2 >= len(tokens): break
                    result[cid] = _unicode(b"<" + tokens[offset + 2] + b">")
    return result


def _decode_identity_h(data: bytes) -> list[int]:
    if len(data) % 2: raise ValueError(f"Identity-H text has odd byte count: {len(data)}")
    return [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data), 2)]


def _decode_bytes(data: bytes, cmap: dict[int, str]) -> tuple[str, list[int]]:
    parts: list[str] = []; missing: list[int] = []
    for cid in _decode_identity_h(data):
        if cid in cmap: parts.append(cmap[cid])
        else: missing.append(cid); parts.append(f"⟦CID:{cid}⟧")
    return "".join(parts), missing


def decode_text_show(operation: TextShow, cmap: dict[int, str]) -> tuple[str, list[int]]:
    if operation.operator == b"TJ":
        parts: list[str] = []; missing: list[int] = []
        for value in operation.value:
            if isinstance(value, bytes):
                text, absent = _decode_bytes(value, cmap); parts.append(text); missing.extend(absent)
        return "".join(parts), missing
    return _decode_bytes(operation.value, cmap)


def _font_descriptor_info(objects: dict[int, bytes], font_ref: int) -> dict[str, object]:
    body = objects.get(font_ref, b"")
    descendant = _dict_value(body, b"DescendantFonts")
    refs = re.findall(rb"(\d+)\s+\d+\s+R", descendant or b"")
    descendant_ref = int(refs[0]) if refs else None
    descendant_body = objects.get(descendant_ref, b"") if descendant_ref is not None else b""
    descriptor = _dict_value(descendant_body, b"FontDescriptor")
    refs = re.findall(rb"(\d+)\s+\d+\s+R", descriptor or b"")
    descriptor_ref = int(refs[0]) if refs else None
    descriptor_body = objects.get(descriptor_ref, b"") if descriptor_ref is not None else b""
    tounicode = _dict_value(body, b"ToUnicode")
    refs = re.findall(rb"(\d+)\s+\d+\s+R", tounicode or b"")
    tounicode_ref = int(refs[0]) if refs else None
    base = re.search(rb"/BaseFont\s*/([^\s/>]+)", body)
    encoding = re.search(rb"/Encoding\s*/([^\s/>]+)", body)
    return {"font_object": font_ref, "base_font": base.group(1).decode("latin1") if base else "", "encoding": encoding.group(1).decode("latin1") if encoding else "", "descendant_font": descendant_ref, "font_descriptor": descriptor_ref, "font_file2": bool(re.search(rb"/FontFile2\s+\d+\s+\d+\s+R", descriptor_body)), "tounicode": tounicode_ref}


def _page_operations(objects: dict[int, bytes], page_body: bytes) -> Iterable[tuple[int, bytes, list[TextShow]]]:
    for content_num in _contents_refs(page_body):
        body = objects.get(content_num)
        if body is None: continue
        decoded = _decode_stream(body)
        if decoded is None: continue
        try: operations = extract_text_show_operations(decoded)
        except Exception: operations = []
        if not operations: operations = _fallback_text_operations(decoded)
        yield content_num, decoded, operations


def _load_overrides(path: Path | None, base_font: str) -> dict[int, str]:
    if path is None or not path.exists(): return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    section = data.get(base_font, {})
    return {int(cid): str(text) for cid, text in section.items()}


def analyze_page(pdf_path: Path, page_number: int, mapping_path: Path | None = None) -> dict[str, object]:
    pdf = pdf_path.read_bytes(); objects = _object_map(pdf); pages = _page_objects(objects)
    if page_number < 1 or page_number > len(pages): raise ValueError(f"page {page_number} outside 1..{len(pages)}")
    page_object, page_body = pages[page_number - 1]
    resources = _resources_value(page_body, b"Resources"); fonts = _font_refs(resources)
    mappings: dict[int, str] = {}; font_info: dict[str, object] = {}
    for resource_name, font_ref in fonts.items():
        info = _font_descriptor_info(objects, font_ref); font_info[resource_name.decode("latin1")] = info
        cmap_ref = info.get("tounicode")
        if isinstance(cmap_ref, int):
            stream = _decode_stream(objects.get(cmap_ref, b""))
            if stream: mappings.update(parse_tounicode(stream))
        base_font = str(info.get("base_font", ""))
        mappings.update(_load_overrides(mapping_path, base_font))
    shows: list[dict[str, object]] = []; used: dict[int, int] = {}
    for content_num, _decoded, operations in _page_operations(objects, page_body):
        for index, operation in enumerate(operations, 1):
            text, missing = decode_text_show(operation, mappings)
            cids: list[int] = []
            values = operation.value if operation.operator == b"TJ" else (operation.value,)
            for value in values:
                if isinstance(value, bytes): cids.extend(_decode_identity_h(value))
            for cid in cids: used[cid] = used.get(cid, 0) + 1
            shows.append({"content_object": content_num, "index": index, "operator": operation.operator.decode("latin1"), "cids": cids, "decoded": text, "missing_cids": missing})
    return {"pdf": str(pdf_path), "page": page_number, "page_object": page_object, "fonts": font_info, "tounicode_entries": len(mappings), "used_cids": {str(k): v for k, v in sorted(used.items())}, "missing_cids": sorted(cid for cid in used if cid not in mappings), "operations": shows}


def extract_pdf(path: str | Path, page: int | None = None, json_output: Path | None = None, mapping: Path | None = None) -> int:
    pdf_path = Path(path)
    if page is not None:
        report = analyze_page(pdf_path, page, mapping)
        if json_output: json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"PDF: {pdf_path}\nPAGE: {page}\nToUnicode/custom mappings: {report['tounicode_entries']}\nUsed CIDs: {len(report['used_cids'])}\nUnresolved CIDs: {report['missing_cids']}\n\nDECODED TEXT")
        for operation in report["operations"]: print(operation["decoded"])
        return 0
    pdf = pdf_path.read_bytes(); objects = _object_map(pdf); pages = _page_objects(objects)
    print(f"PDF: {pdf_path}\nObjects: {len(objects)}\nPages: {len(pages)}")
    for index in range(1, len(pages) + 1):
        report = analyze_page(pdf_path, index, mapping); print(f"\n=== PAGE {index} ===\nMappings: {report['tounicode_entries']}\nMissing CIDs: {report['missing_cids']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Decode Identity-H PDF text using ToUnicode plus validated custom mappings")
    parser.add_argument("pdf", type=Path); parser.add_argument("--page", type=int); parser.add_argument("--json", dest="json_output", type=Path); parser.add_argument("--mapping", type=Path, help="validated custom_glyph_map.json")
    args = parser.parse_args(); return extract_pdf(args.pdf, args.page, args.json_output, args.mapping)


if __name__ == "__main__":
    raise SystemExit(main())

"""Robust direct parser for EC PDFs whose page dictionaries use indirect Resources."""
from __future__ import annotations

import json
import re
import zlib
from io import BytesIO
from pathlib import Path
from typing import Any

from fontTools.ttLib import TTFont

from .learned_mapping import font_key, load_database

OBJ_RE = re.compile(rb"(?ms)(\d+)\s+(\d+)\s+obj\s*(.*?)\s*endobj")
REF_RE = re.compile(rb"(\d+)\s+(\d+)\s+R")
HEX_RE = re.compile(rb"<([0-9A-Fa-f]+)>")
GLYPH_UNI_RE = re.compile(r"uni([0-9A-Fa-f]{4,6})")
GLYPH_U_RE = re.compile(r"(?:^|[._])u([0-9A-Fa-f]{4,6})(?:$|[._])")


def object_map(pdf: bytes) -> dict[int, bytes]:
    return {int(m.group(1)): m.group(3) for m in OBJ_RE.finditer(pdf)}


def dict_value(body: bytes | None, key: bytes) -> bytes | None:
    if not body:
        return None
    m = re.search(rb"/" + re.escape(key) + rb"(?:\s*)?(\[.*?\]|<<.*?>>|\d+\s+\d+\s+R|/[A-Za-z0-9_.+#-]+|[+-]?\d+(?:\.\d+)?)", body, re.S)
    return m.group(1) if m else None


def first_ref(value: bytes | None) -> int | None:
    if not value:
        return None
    m = REF_RE.search(value)
    return int(m.group(1)) if m else None


def resolve(objects: dict[int, bytes], value: bytes | None, max_depth: int = 8) -> bytes | None:
    current = value
    for _ in range(max_depth):
        ref = first_ref(current)
        if ref is None:
            return current
        nxt = objects.get(ref)
        if nxt is None:
            return None
        current = nxt
    return current


def stream_bytes(body: bytes | None) -> bytes | None:
    if not body:
        return None
    m = re.search(rb"\bstream(?:\r\n|\n|\r)", body)
    if not m:
        return None
    end = body.find(b"endstream", m.end())
    if end < 0:
        return None
    data = body[m.end():end].rstrip(b"\r\n")
    filt = dict_value(body, b"Filter") or b""
    if b"FlateDecode" in filt:
        return zlib.decompress(data)
    return data


def pages(objects: dict[int, bytes]) -> list[tuple[int, bytes]]:
    return sorted((n, b) for n, b in objects.items() if re.search(rb"/Type\s*/Page(?:\s|/|>)", b))


def content_refs(page_body: bytes) -> list[int]:
    value = dict_value(page_body, b"Contents")
    return [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", value or b"")]


def page_resources(objects: dict[int, bytes], page_body: bytes) -> bytes | None:
    return resolve(objects, dict_value(page_body, b"Resources"))


def font_refs(objects: dict[int, bytes], resources: bytes | None) -> dict[bytes, int]:
    resources = resolve(objects, resources)
    font_dict = resolve(objects, dict_value(resources, b"Font"))
    if not font_dict:
        return {}
    return {name: int(ref) for name, ref in re.findall(rb"/(\S+)\s+(\d+)\s+\d+\s+R", font_dict)}


def font_info(objects: dict[int, bytes], font_ref: int) -> dict[str, Any]:
    body = objects.get(font_ref, b"")
    descendant_value = dict_value(body, b"DescendantFonts")
    descendant_ref = first_ref(descendant_value)
    descendant = resolve(objects, descendant_value)
    descriptor_value = dict_value(descendant, b"FontDescriptor")
    descriptor_ref = first_ref(descriptor_value)
    descriptor = resolve(objects, descriptor_value)
    ff_value = dict_value(descriptor, b"FontFile2")
    ff_ref = first_ref(ff_value)
    tu_value = dict_value(body, b"ToUnicode")
    tu_ref = first_ref(tu_value)
    base = re.search(rb"/BaseFont\s*/([^\s/>]+)", body)
    enc = re.search(rb"/Encoding\s*/([^\s/>]+)", body)
    return {"font_object": font_ref, "base_font": base.group(1).decode("latin1") if base else "", "encoding": enc.group(1).decode("latin1") if enc else "", "descendant_font": descendant_ref, "font_descriptor": descriptor_ref, "font_file2": ff_ref, "tounicode": tu_ref}


def embedded_fonts(pdf: bytes, page: int):
    objects = object_map(pdf)
    pgs = pages(objects)
    if not 1 <= page <= len(pgs):
        raise ValueError(f"page {page} outside 1..{len(pgs)}")
    resources = page_resources(objects, pgs[page - 1][1])
    for resource, ref in font_refs(objects, resources).items():
        info = font_info(objects, ref)
        ff_ref = info.get("font_file2")
        if not isinstance(ff_ref, int):
            continue
        raw = stream_bytes(objects.get(ff_ref))
        if raw:
            yield resource.decode("latin1"), str(info["base_font"]), raw


def _glyph_name_text(name: str) -> str | None:
    """Decode Unicode encoded in a TrueType glyph name.

    EC embedded fonts often have a perfectly useful glyph name such as
    ``uni09C7`` even though that glyph has no entry in the font cmap because
    the PDF is using Identity-H/CID addressing.  Therefore cmap-only lookup
    is insufficient.  We also understand compound names such as
    ``uni0995_uni09CD_uni09A4`` and ``uni0995.1``.
    """
    if name in {".notdef", "space"}:
        return None

    # Adobe-style names: uni09A8_uni09CD_uni09A4, uni09A8.123, etc.
    parts = re.findall(r"uni([0-9A-Fa-f]{4,6})", name)
    if parts:
        try:
            return "".join(chr(int(part, 16)) for part in parts)
        except ValueError:
            pass

    # Some fonts use u09A8 or u1D00 style names.
    match = GLYPH_U_RE.search(name)
    if match:
        try:
            return chr(int(match.group(1), 16))
        except ValueError:
            return None
    return None


def ttf_gid_map(font_bytes: bytes) -> dict[int, str]:
    """Return GID -> Unicode using BOTH cmap and glyph-name evidence.

    The glyph-name fallback is essential for this EC font family: ordinary
    Bengali glyphs are Unicode-named but the embedded font's cmap does not
    necessarily expose those names to fontTools' best-cmap selection.
    Manual learned mappings still override this fallback later.
    """
    font = TTFont(BytesIO(font_bytes), lazy=False)
    try:
        out: dict[int, str] = {}
        glyph_order = font.getGlyphOrder()
        reverse = font.getReverseGlyphMap()

        # 1. Explicit Unicode cmap.
        for cp, name in font.getBestCmap().items():
            gid = reverse.get(name)
            if gid is not None:
                out.setdefault(gid, chr(cp))

        # 2. Unicode encoded in glyph names.  Do this for every GID because
        # Identity-H PDF fonts may have no usable Unicode cmap at all.
        for gid, name in enumerate(glyph_order):
            text = _glyph_name_text(name)
            if text:
                out.setdefault(gid, text)
        return out
    finally:
        font.close()


def hex_bytes(token: bytes) -> bytes:
    raw = token.strip()[1:-1]
    if len(raw) & 1:
        raw += b"0"
    return bytes.fromhex(raw.decode("ascii"))


def cid(token: bytes) -> int:
    return int.from_bytes(hex_bytes(token), "big")


def uni(token: bytes) -> str:
    raw = hex_bytes(token)
    if len(raw) & 1:
        raw += b"\x00"
    return raw.decode("utf-16-be", errors="strict")


def parse_tounicode(data: bytes) -> dict[int, str]:
    out: dict[int, str] = {}
    for block in re.finditer(rb"beginbfchar\s*(.*?)\s*endbfchar", data, re.S):
        for line in block.group(1).splitlines():
            toks = HEX_RE.findall(line)
            if len(toks) >= 2:
                out[cid(b"<" + toks[0] + b">")] = uni(b"<" + toks[1] + b">")
    for block in re.finditer(rb"beginbfrange\s*(.*?)\s*endbfrange", data, re.S):
        for line in block.group(1).splitlines():
            toks = HEX_RE.findall(line)
            if len(toks) < 3:
                continue
            start = cid(b"<" + toks[0] + b">")
            end = cid(b"<" + toks[1] + b">")
            if start > end:
                continue
            base = hex_bytes(b"<" + toks[2] + b">")
            for offset, value in enumerate(range(start, end + 1)):
                if len(toks) == 3:
                    out[value] = (int.from_bytes(base, "big") + offset).to_bytes(len(base), "big").decode("utf-16-be", errors="strict")
                elif offset + 2 < len(toks):
                    out[value] = uni(b"<" + toks[offset + 2] + b">")
    return out


def text_shows(objects: dict[int, bytes], page_body: bytes):
    from .content import extract_text_show_operations
    found = []
    for number in content_refs(page_body):
        body = objects.get(number)
        data = stream_bytes(body)
        if not data:
            continue
        try:
            ops = extract_text_show_operations(data)
        except Exception:
            ops = []
        found.extend((number, op) for op in ops)
    return found


def identity_cids(data: bytes) -> list[int]:
    return [int.from_bytes(data[i:i + 2], "big") for i in range(0, len(data) - 1, 2)]


def decode_operation(op, mapping: dict[int, str]):
    values = op.value if op.operator == b"TJ" else (op.value,)
    parts: list[str] = []
    missing: list[int] = []
    cids: list[int] = []
    for value in values:
        if not isinstance(value, bytes):
            continue
        ids = identity_cids(value)
        cids.extend(ids)
        for x in ids:
            if x in mapping:
                parts.append(mapping[x])
            else:
                missing.append(x)
                parts.append(f"⟦CID:{x}⟧")
    return "".join(parts), cids, missing


def _mapping_section(data: dict[str, Any], base_font: str) -> dict[str, Any]:
    """Find a manual mapping despite PDF subset-prefix spelling differences."""
    if not isinstance(data, dict):
        return {}
    candidates = [base_font, base_font.lstrip("/")]
    if "+" in base_font:
        candidates.append(base_font.split("+", 1)[1])
    for key in candidates:
        section = data.get(key)
        if isinstance(section, dict):
            return section
    normalized = base_font.lstrip("/").split("+", 1)[-1]
    for key, section in data.items():
        if isinstance(key, str) and key.lstrip("/").split("+", 1)[-1] == normalized and isinstance(section, dict):
            return section
    return {}


def _apply_mapping_section(mappings: dict[int, str], section: dict[str, Any]) -> None:
    for key, value in section.items():
        try:
            gid = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, str):
            mappings[gid] = value
        elif isinstance(value, dict) and isinstance(value.get("text"), str):
            mappings[gid] = value["text"]


def analyze_page_direct(pdf_path: Path, page_number: int, mapping_path: Path | None = None) -> dict[str, Any]:
    pdf = pdf_path.read_bytes()
    objects = object_map(pdf)
    pgs = pages(objects)
    if not 1 <= page_number <= len(pgs):
        raise ValueError(f"page {page_number} outside 1..{len(pgs)}")
    page_object, page_body = pgs[page_number - 1]
    resources = page_resources(objects, page_body)
    mappings: dict[int, str] = {}
    fonts_out: dict[str, Any] = {}
    override_data: dict[str, Any] = {}
    if mapping_path and mapping_path.exists():
        loaded = json.loads(mapping_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            override_data = loaded

    for resource, ref in font_refs(objects, resources).items():
        info = font_info(objects, ref)
        fonts_out[resource.decode("latin1")] = info
        tu_ref = info.get("tounicode")
        if isinstance(tu_ref, int):
            data = stream_bytes(objects.get(tu_ref))
            if data:
                mappings.update(parse_tounicode(data))

        base = str(info.get("base_font", ""))
        # Manual/custom mappings override ToUnicode.  This is required for
        # EC's custom conjunct glyphs such as CID 207 and CID 290.
        _apply_mapping_section(mappings, _mapping_section(override_data, base))

        ff_ref = info.get("font_file2")
        if isinstance(ff_ref, int):
            raw = stream_bytes(objects.get(ff_ref))
            if raw:
                for gid, text in ttf_gid_map(raw).items():
                    mappings.setdefault(gid, text)

    shows = []
    used: dict[int, int] = {}
    for number, op in text_shows(objects, page_body):
        decoded, cids, missing = decode_operation(op, mappings)
        for x in cids:
            used[x] = used.get(x, 0) + 1
        shows.append({"content_object": number, "operator": op.operator.decode("latin1"), "cids": cids, "decoded": decoded, "missing_cids": missing})

    missing_cids = sorted(x for x in used if x not in mappings)
    return {"pdf": str(pdf_path), "page": page_number, "page_object": page_object, "fonts": fonts_out, "tounicode_entries": len(mappings), "used_cids": {str(k): v for k, v in sorted(used.items())}, "missing_cids": missing_cids, "operations": shows}


def used_gids(pdf: bytes, page: int) -> list[int]:
    objects = object_map(pdf)
    pgs = pages(objects)
    page_body = pgs[page - 1][1]
    used = set()
    for _number, op in text_shows(objects, page_body):
        values = op.value if op.operator == b"TJ" else (op.value,)
        for value in values:
            if isinstance(value, bytes):
                used.update(identity_cids(value))
    return sorted(used)


def learned_override_data(path: Path) -> dict[str, Any]:
    return load_database(path)


def apply_learned_to_mapping(mapping: dict[str, dict[str, str]], pdf: bytes, page: int, db_path: Path) -> None:
    db = load_database(db_path)
    for _resource, base_font, raw in embedded_fonts(pdf, page):
        fkey = font_key(raw, base_font)
        section = mapping.setdefault(base_font, {})
        glyphs = db.get("fonts", {}).get(fkey, {}).get("glyphs", {})
        for gid, entry in glyphs.items():
            if isinstance(entry, dict) and entry.get("confidence", 0) >= 1.0:
                section[str(gid)] = str(entry.get("text", ""))

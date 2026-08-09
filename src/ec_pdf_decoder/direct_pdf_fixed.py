"""Safety wrapper around direct_pdf's resolver for nested PDF dictionaries.

The EC page Resources dictionary is a direct ``<<...>>`` value containing
nested indirect references.  The original generic resolver searched for the
first ``N 0 R`` anywhere in that dictionary, so it accidentally resolved the
ExtGState reference (15 0 R) instead of leaving the Resources dictionary
intact.  That made /Font discovery return zero entries.

This module keeps the existing direct parser but replaces only the unsafe
reference resolution semantics.  A direct dictionary is never treated as an
indirect reference merely because it contains an ``N 0 R`` somewhere inside.
Single-reference arrays such as ``[235 0 R]`` are still resolved because
Type0 DescendantFonts uses that PDF form.
"""
from __future__ import annotations

import re

from . import direct_pdf as _direct


def _strict_first_ref(value: bytes | None) -> int | None:
    if not value:
        return None
    stripped = value.strip()
    match = re.fullmatch(rb"(\d+)\s+(\d+)\s+R", stripped)
    if match:
        return int(match.group(1))

    # DescendantFonts is normally a one-element indirect-reference array.
    array = re.fullmatch(rb"\[\s*(\d+)\s+(\d+)\s+R\s*\]", stripped, re.S)
    if array:
        return int(array.group(1))
    return None


def resolve(objects: dict[int, bytes], value: bytes | None, max_depth: int = 8) -> bytes | None:
    current = value
    for _ in range(max_depth):
        ref = _strict_first_ref(current)
        if ref is None:
            return current
        nxt = objects.get(ref)
        if nxt is None:
            return None
        current = nxt
    return current


def page_resources(objects: dict[int, bytes], page_body: bytes) -> bytes | None:
    # Reuse the balanced Resources scanner, but use the safe resolver when
    # Resources is itself an indirect reference.
    marker = re.search(rb"/Resources\s*", page_body)
    if not marker:
        return None

    pos = marker.end()
    if page_body[pos:pos + 2] == b"<<":
        start = pos
        depth = 0
        i = pos
        while i < len(page_body) - 1:
            pair = page_body[i:i + 2]
            if pair == b"<<":
                depth += 1
                i += 2
                continue
            if pair == b">>":
                depth -= 1
                i += 2
                if depth == 0:
                    return page_body[start:i]
                continue
            i += 1
        return None

    value = _direct.dict_value(page_body, b"Resources")
    return resolve(objects, value)


def font_refs(objects: dict[int, bytes], resources: bytes | None) -> dict[bytes, int]:
    resources = resolve(objects, resources) or b""
    result: dict[bytes, int] = {}

    result.update(dict(_direct._resource_font_entries(resources)))

    if not result:
        font_value = _direct.dict_value(resources, b"Font")
        font_dict = resolve(objects, font_value)
        if font_dict:
            result.update(dict(_direct._resource_font_entries(font_dict)))

    return result


def font_info(objects: dict[int, bytes], font_ref: int):
    body = objects.get(font_ref, b"")
    descendant_value = _direct.dict_value(body, b"DescendantFonts")
    descendant_ref = _direct.first_ref(descendant_value)
    descendant = resolve(objects, descendant_value)
    descriptor_value = _direct.dict_value(descendant, b"FontDescriptor")
    descriptor_ref = _direct.first_ref(descriptor_value)
    descriptor = resolve(objects, descriptor_value)
    ff_value = _direct.dict_value(descriptor, b"FontFile2")
    ff_ref = _direct.first_ref(ff_value)
    tu_value = _direct.dict_value(body, b"ToUnicode")
    tu_ref = _direct.first_ref(tu_value)
    base = re.search(rb"/BaseFont\s*/([^\s/>]+)", body)
    enc = re.search(rb"/Encoding\s*/([^\s/>]+)", body)
    return {
        "font_object": font_ref,
        "base_font": base.group(1).decode("latin1") if base else "",
        "encoding": enc.group(1).decode("latin1") if enc else "",
        "descendant_font": descendant_ref,
        "font_descriptor": descriptor_ref,
        "font_file2": ff_ref,
        "tounicode": tu_ref,
    }


def embedded_fonts(pdf: bytes, page: int):
    objects = _direct.object_map(pdf)
    pgs = _direct.pages(objects)
    if not 1 <= page <= len(pgs):
        raise ValueError(f"page {page} outside 1..{len(pgs)}")
    resources = page_resources(objects, pgs[page - 1][1])
    for resource, ref in font_refs(objects, resources).items():
        info = font_info(objects, ref)
        ff_ref = info.get("font_file2")
        if not isinstance(ff_ref, int):
            continue
        raw = _direct.stream_bytes(objects.get(ff_ref))
        if raw:
            yield resource.decode("latin1"), str(info["base_font"]), raw


def analyze_page_direct(pdf_path, page_number, mapping_path=None):
    # Use the original analysis implementation with the corrected module
    # functions injected into its globals.  No decoding logic is changed.
    original_resolve = _direct.resolve
    original_page_resources = _direct.page_resources
    original_font_refs = _direct.font_refs
    original_font_info = _direct.font_info
    original_embedded_fonts = _direct.embedded_fonts
    try:
        _direct.resolve = resolve
        _direct.page_resources = page_resources
        _direct.font_refs = font_refs
        _direct.font_info = font_info
        _direct.embedded_fonts = embedded_fonts
        return _direct.analyze_page_direct(pdf_path, page_number, mapping_path)
    finally:
        _direct.resolve = original_resolve
        _direct.page_resources = original_page_resources
        _direct.font_refs = original_font_refs
        _direct.font_info = original_font_info
        _direct.embedded_fonts = original_embedded_fonts


# Re-export helpers used by diagnostics and decoder modules.
object_map = _direct.object_map
pages = _direct.pages
stream_bytes = _direct.stream_bytes
ttf_gid_map = _direct.ttf_gid_map
used_gids = _direct.used_gids
learned_override_data = _direct.learned_override_data
apply_learned_to_mapping = _direct.apply_learned_to_mapping

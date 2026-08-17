"""Diagnostic reporting for manual --debug-genglyph experiments.

This module deliberately does not change mappings or generate SVGs. It reports
what the PDF's embedded font and PDF content stream actually say about a
requested Unicode candidate, including the real CIDToGIDMap used by the font.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import direct_pdf as _direct
from .bangla_harfbuzz import shape_gids
from .direct_pdf_fixed import embedded_fonts, ttf_gid_map, used_gids


def _candidate_status(text: str, candidate_path: Path = Path("data/bangla_conjuncts_comprehensive_validated.json")) -> str:
    if not candidate_path.exists():
        return "candidate database not found"
    try:
        data = json.loads(candidate_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"candidate database unreadable: {exc}"
    for item in data.get("conjuncts", []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("glyph") == text:
            return f"candidate database: YES ({item.get('combination', '')})"
    return "candidate database: NO"


def _first_pdf_ref(value: bytes | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(rb"\s*(\d+)\s+(\d+)\s+R\s*", value, re.S)
    if match:
        return int(match.group(1))
    match = re.fullmatch(rb"\s*\[\s*(\d+)\s+(\d+)\s+R\s*\]\s*", value, re.S)
    if match:
        return int(match.group(1))
    match = re.search(rb"(?:^|\s)(\d+)\s+(\d+)\s+R(?:\s|$)", value, re.S)
    if match:
        return int(match.group(1))
    return None


def _resolve_pdf_value(objects: dict[int, bytes], value: bytes | None) -> tuple[bytes | None, int | None]:
    current = value
    ref = _first_pdf_ref(current)
    for _ in range(8):
        if ref is None:
            return current, None
        current = objects.get(ref)
        if current is None:
            return None, ref
        next_ref = _first_pdf_ref(current)
        if next_ref is None:
            return current, ref
        ref = next_ref
    return current, ref


def _cid_to_gid_maps(pdf: bytes, page: int) -> tuple[dict[str, dict], list[str]]:
    """Read the PDF /CIDToGIDMap for the fonts used on one page.

    The diagnostic intentionally uses a strict PDF-reference parser here rather
    than treating any N 0 R buried inside a dictionary as the dictionary's
    reference. This also prints the font/descendant structure when the map is
    unavailable, so the next debugging step is evidence-based.
    """
    objects = _direct.object_map(pdf)
    pgs = _direct.pages(objects)
    if not 1 <= page <= len(pgs):
        raise ValueError(f"page {page} outside 1..{len(pgs)}")

    page_body = pgs[page - 1][1]
    resources = _direct.page_resources(objects, page_body)
    font_refs = _direct.font_refs(objects, resources)
    used = sorted(set(int(cid) for cid in used_gids(pdf, page)))
    result: dict[str, dict] = {}
    warnings: list[str] = []

    for resource, font_ref in font_refs.items():
        resource_name = resource.decode("latin1")
        font_body = objects.get(font_ref, b"")
        descendant_value = _direct.dict_value(font_body, b"DescendantFonts")
        descendant_ref = _first_pdf_ref(descendant_value)
        descendant, resolved_desc_ref = _resolve_pdf_value(objects, descendant_value)

        if not descendant:
            result[resource_name] = {
                "mode": "unavailable",
                "map": {},
                "font_object": font_ref,
                "descendant_ref": descendant_ref,
                "resolved_desc_ref": resolved_desc_ref,
                "descendant_value": descendant_value,
            }
            continue

        cidmap_value = _direct.dict_value(descendant, b"CIDToGIDMap")
        if not cidmap_value:
            result[resource_name] = {
                "mode": "missing",
                "map": {},
                "font_object": font_ref,
                "descendant_ref": descendant_ref,
                "resolved_desc_ref": resolved_desc_ref,
                "descendant_value": descendant_value,
                "cidmap_value": cidmap_value,
            }
            continue

        stripped = cidmap_value.strip()
        if stripped == b"/Identity":
            result[resource_name] = {
                "mode": "Identity",
                "map": {str(cid): cid for cid in used},
                "font_object": font_ref,
                "descendant_ref": descendant_ref,
                "resolved_desc_ref": resolved_desc_ref,
                "cidmap_value": cidmap_value,
            }
            continue

        cidmap_ref = _first_pdf_ref(cidmap_value)
        if cidmap_ref is None:
            result[resource_name] = {
                "mode": "unavailable",
                "map": {},
                "font_object": font_ref,
                "descendant_ref": descendant_ref,
                "resolved_desc_ref": resolved_desc_ref,
                "cidmap_value": cidmap_value,
            }
            continue

        stream = _direct.stream_bytes(objects.get(cidmap_ref))
        if not stream:
            warnings.append(f"{resource_name}: CIDToGIDMap object {cidmap_ref} has no readable stream")
            result[resource_name] = {
                "mode": "embedded",
                "map": {},
                "object": cidmap_ref,
                "font_object": font_ref,
                "descendant_ref": descendant_ref,
                "resolved_desc_ref": resolved_desc_ref,
                "cidmap_value": cidmap_value,
            }
            continue

        mapping: dict[str, int | None] = {}
        for cid in used:
            offset = cid * 2
            mapping[str(cid)] = int.from_bytes(stream[offset:offset + 2], "big") if offset + 1 < len(stream) else None
        result[resource_name] = {
            "mode": "embedded",
            "object": cidmap_ref,
            "bytes": len(stream),
            "map": mapping,
            "font_object": font_ref,
            "descendant_ref": descendant_ref,
            "resolved_desc_ref": resolved_desc_ref,
            "cidmap_value": cidmap_value,
        }

    return result, warnings


def debug_genglyph(pdf: bytes, page: int, text: str) -> None:
    if not text:
        raise ValueError("debug-genglyph text cannot be empty")

    print("DEBUG GENGLYPH")
    print("==============")
    print(f"PAGE: {page}")
    print(f"TARGET: {text!r}")
    print(_candidate_status(text))

    cidmaps, warnings = _cid_to_gid_maps(pdf, page)
    if warnings:
        print("CIDToGIDMap WARNINGS:")
        for warning in warnings:
            print(f"  {warning}")

    seen_font = False
    for resource, base_font, raw in embedded_fonts(pdf, page):
        seen_font = True
        cmap = ttf_gid_map(raw)
        used = sorted(set(int(cid) for cid in used_gids(pdf, page)))
        shaped = shape_gids(raw, text)
        unresolved_cids = [cid for cid in used if cid not in cmap]

        print()
        print(f"PDF RESOURCE: {resource}")
        print(f"EMBEDDED FONT: {base_font}")
        print(f"EMBEDDED FONT GID COUNT: {len(cmap)} mapped-by-cmap/name entries")
        print(f"PDF USED CIDS: {used}")
        print(f"PDF UNMAPPED CIDS: {unresolved_cids}")
        print(f"EMBEDDED FONT HARFBUZZ GIDS FOR {text!r}: {shaped}")

        if shaped:
            for gid in shaped:
                print(f"  SHAPED TTF GID {gid}")

        info = cidmaps.get(resource)
        if info is None:
            print("CIDToGIDMap: unavailable")
            continue

        print("CIDToGIDMap:")
        print(f"  MODE: {info.get('mode')}")
        print(f"  FONT OBJECT: {info.get('font_object')}")
        print(f"  DESCENDANT REF: {info.get('descendant_ref')}")
        print(f"  RESOLVED DESCENDANT REF: {info.get('resolved_desc_ref')}")
        raw_desc = info.get("descendant_value")
        if raw_desc:
            print(f"  DESCENDANT VALUE: {raw_desc[:200]!r}")
        raw_cidmap = info.get("cidmap_value")
        if raw_cidmap:
            print(f"  RAW CIDTOGIDMAP VALUE: {raw_cidmap!r}")
        if info.get("object") is not None:
            print(f"  OBJECT: {info['object']} ({info.get('bytes', 0)} bytes)")
        mapping = info.get("map", {})
        for cid in used:
            print(f"  CID {cid} -> TTF GID {mapping.get(str(cid))}")

        print("UNMAPPED PDF GLYPH DETAILS:")
        for cid in unresolved_cids:
            print(
                f"  PDF CID {cid}: cmap/name={cmap.get(cid)!r}; "
                f"CIDToGIDMap GID={mapping.get(str(cid))}"
            )

    if not seen_font:
        print()
        print("NO EMBEDDED FontFile2 RESOURCE FOUND ON THE SELECTED PAGE")

    print()
    print("NOTE: this command is diagnostic only. It does not modify mappings or SVG files.")

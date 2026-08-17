"""Diagnostic reporting for manual --debug-genglyph experiments.

This module deliberately does not change mappings or generate SVGs. It reports
what the PDF's embedded font and PDF content stream actually say about a
requested Unicode candidate, including the real CIDToGIDMap used by the font.
"""
from __future__ import annotations

import json
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


def _cid_to_gid_maps(pdf: bytes, page: int) -> tuple[dict[str, dict], list[str]]:
    """Read the PDF /CIDToGIDMap for the fonts used on one page."""
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
        descendant = _direct.resolve(objects, descendant_value)
        if not descendant:
            result[resource_name] = {"mode": "unavailable", "map": {}}
            continue

        cidmap_value = _direct.dict_value(descendant, b"CIDToGIDMap")
        if not cidmap_value:
            result[resource_name] = {"mode": "missing", "map": {}}
            continue

        stripped = cidmap_value.strip()
        if stripped == b"/Identity":
            result[resource_name] = {"mode": "Identity", "map": {str(cid): cid for cid in used}}
            continue

        cidmap_ref = _direct.first_ref(cidmap_value)
        if cidmap_ref is None:
            result[resource_name] = {"mode": "unavailable", "map": {}}
            continue

        stream = _direct.stream_bytes(objects.get(cidmap_ref))
        if not stream:
            warnings.append(f"{resource_name}: CIDToGIDMap object {cidmap_ref} has no readable stream")
            result[resource_name] = {"mode": "embedded", "map": {}, "object": cidmap_ref}
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
        else:
            print("CIDToGIDMap:")
            print(f"  MODE: {info.get('mode')}")
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

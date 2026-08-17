"""Diagnostic reporting for manual --debug-genglyph experiments.

This module deliberately does not change mappings or generate SVGs. It reports
what the PDF's embedded font and PDF content stream actually say about a
requested Unicode candidate so we can design --genglyph from evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

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


def debug_genglyph(pdf: bytes, page: int, text: str) -> None:
    if not text:
        raise ValueError("debug-genglyph text cannot be empty")

    print("DEBUG GENGLYPH")
    print("==============")
    print(f"PAGE: {page}")
    print(f"TARGET: {text!r}")
    print(f"{_candidate_status(text)}")

    seen_font = False
    for resource, base_font, raw in embedded_fonts(pdf, page):
        seen_font = True
        cmap = ttf_gid_map(raw)
        used = sorted(set(int(g) for g in used_gids(pdf, page)))
        unresolved = [gid for gid in used if gid not in cmap]
        shaped = shape_gids(raw, text)

        print()
        print(f"PDF RESOURCE: {resource}")
        print(f"EMBEDDED FONT: {base_font}")
        print(f"EMBEDDED FONT GID COUNT: {len(cmap)} mapped-by-cmap/name entries")
        print(f"PDF USED GIDS/CIDS: {used}")
        print(f"PDF UNMAPPED USED GIDS/CIDS: {unresolved}")
        print(f"EMBEDDED FONT HARFBUZZ GIDS FOR {text!r}: {shaped}")

        if shaped:
            for gid in shaped:
                name = "?"
                if 0 <= gid < len(cmap):
                    name = cmap.get(gid, "<no cmap/name text>")
                print(f"  SHAPED GID {gid}: {name!r}")

        if unresolved:
            print("UNMAPPED PDF GLYPH DETAILS:")
            for gid in unresolved:
                print(f"  PDF GID/CID {gid}: cmap/name={cmap.get(gid)!r}")
        else:
            print("UNMAPPED PDF GLYPH DETAILS: none")

    if not seen_font:
        print()
        print("NO EMBEDDED FontFile2 RESOURCE FOUND ON THE SELECTED PAGE")

    print()
    print("NOTE: this command is diagnostic only. It does not modify mappings or SVG files.")

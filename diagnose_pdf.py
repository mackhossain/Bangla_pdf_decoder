"""Diagnostic for EC PDF page font discovery."""
from __future__ import annotations
import re
import sys
from pathlib import Path
from src.ec_pdf_decoder.direct_pdf_fixed import object_map, pages, page_resources, font_refs, font_info, embedded_fonts, ttf_gid_map


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python diagnose_pdf.py FILE.pdf PAGE")
        return 2
    path = Path(sys.argv[1])
    page = int(sys.argv[2])
    pdf = path.read_bytes()
    objects = object_map(pdf)
    pgs = pages(objects)
    print(f"PDF_BYTES={len(pdf)}")
    print(f"OBJECTS={len(objects)}")
    print(f"PAGES={len(pgs)}")
    if not 1 <= page <= len(pgs):
        print("PAGE_OUT_OF_RANGE")
        return 2
    page_obj, page_body = pgs[page - 1]
    print(f"PAGE_OBJECT={page_obj}")
    print("PAGE_HAS_RESOURCES=" + str(bool(re.search(rb"/Resources\\b", page_body))))
    print("PAGE_HAS_PARENT=" + str(bool(re.search(rb"/Parent\\s+(\\d+)\\s+\\d+\\s+R", page_body))))
    resources = page_resources(objects, page_body)
    print(f"RESOURCES_FOUND={resources is not None}")
    if resources is not None:
        print("RESOURCES=" + resources[:2000].decode("latin1", errors="replace"))
    refs = font_refs(objects, resources)
    print(f"FONT_REFS={len(refs)}")
    for name, ref in refs.items():
        info = font_info(objects, ref)
        print(f"FONT_RESOURCE={name.decode('latin1', errors='replace')} OBJECT={ref} INFO={info}")
    embedded = list(embedded_fonts(pdf, page))
    print(f"EMBEDDED_FONTS={len(embedded)}")
    for resource, base, raw in embedded:
        try:
            gidmap = ttf_gid_map(raw)
            print(f"EMBEDDED resource={resource} base={base} bytes={len(raw)} glyph_mapped={len(gidmap)}")
            print("KNOWN_GIDS=" + ",".join(map(str, sorted(gidmap)[:140])))
            for gid in (47, 79, 89, 75, 207, 290, 308):
                if gid in gidmap:
                    print(f"GID_{gid}={gidmap[gid]!r}")
                else:
                    print(f"GID_{gid}=UNMAPPED")
        except Exception as exc:
            print(f"TTF_ERROR={type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

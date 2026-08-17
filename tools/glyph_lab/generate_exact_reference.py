from __future__ import annotations

import argparse
from pathlib import Path

from src.ec_pdf_decoder.direct_pdf_fixed import embedded_fonts
from src.ec_pdf_decoder.glyph_dump import _single_gid_svg


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an exact reference SVG from a PDF embedded GID.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--gid", type=int, required=True)
    parser.add_argument("--name", required=True, help="Unicode/name used for the reference filename, e.g. শ্চ")
    parser.add_argument("--out-dir", type=Path, default=Path("tools/glyph_lab/references"))
    args = parser.parse_args()

    raw_font = None
    resource_name = None
    base_font = None
    for resource, font_name, raw in embedded_fonts(args.pdf.read_bytes(), args.page):
        if raw:
            raw_font = raw
            resource_name = resource
            base_font = font_name
            break
    if raw_font is None:
        raise SystemExit(f"No embedded FontFile2 found on page {args.page}")

    import tempfile
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as f:
            f.write(raw_font)
            tmp = Path(f.name)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        svg_path = args.out_dir / f"{args.name}.svg"
        svg_path.write_text(_single_gid_svg(tmp, args.gid), encoding="utf-8", newline="\n")
        meta = args.out_dir / f"{args.name}.json"
        meta.write_text(
            '{\n'
            f'  "name": {args.name!r},\n'
            f'  "pdf": {str(args.pdf)!r},\n'
            f'  "page": {args.page},\n'
            f'  "resource": {str(resource_name)!r},\n'
            f'  "font": {str(base_font)!r},\n'
            f'  "cid_gid": {args.gid}\n'
            '}\n',
            encoding="utf-8",
        )
        print(f"REFERENCE SVG: {svg_path.resolve()}")
        print(f"PDF RESOURCE: {resource_name}")
        print(f"EMBEDDED FONT: {base_font}")
        print(f"CID/GID: {args.gid}")
        return 0
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())

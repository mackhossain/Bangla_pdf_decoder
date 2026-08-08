"""Interactive learner for unresolved custom Bangla PDF glyphs."""
from __future__ import annotations

import argparse
import base64
import json
import re
import tempfile
import webbrowser
from pathlib import Path

from src.ec_pdf_decoder.bangla_candidates import generate_candidates
from src.ec_pdf_decoder.extract import (
    _decode_stream, _dict_value, _font_descriptor_info, _font_refs,
    _object_map, _page_objects, _page_operations, _resources_value,
)
from src.ec_pdf_decoder.learned_mapping import (
    font_key, glyph_key, load_database, remember_mapping, save_database,
)


def embedded_fonts(pdf: bytes, page: int):
    objects = _object_map(pdf)
    pages = _page_objects(objects)
    if page < 1 or page > len(pages):
        raise ValueError(f"page {page} outside 1..{len(pages)}")
    page_body = pages[page - 1][1]
    resources = _resources_value(page_body, b"Resources")
    for resource, ref in _font_refs(resources).items():
        info = _font_descriptor_info(objects, ref)
        descriptor_ref = info.get("font_descriptor")
        descriptor = objects.get(descriptor_ref, b"") if isinstance(descriptor_ref, int) else b""
        ff = _dict_value(descriptor, b"FontFile2")
        refs = re.findall(rb"(\d+)\s+\d+\s+R", ff or b"")
        if not refs:
            continue
        stream = _decode_stream(objects.get(int(refs[0]), b""))
        if stream:
            yield resource.decode("latin1"), str(info.get("base_font", "")), stream


def shape(font_path: Path, text: str):
    import uharfbuzz as hb
    blob = hb.Blob.from_file_path(str(font_path))
    face = hb.Face(blob)
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return [i.codepoint for i in buf.glyph_infos]


def glyph_stats(font_path: Path, gid: int):
    from fontTools.ttLib import TTFont
    font = TTFont(str(font_path), lazy=False)
    try:
        order = font.getGlyphOrder()
        if gid < 0 or gid >= len(order):
            return None
        name = order[gid]
        glyph = font["glyf"][name]
        coords, end_points, _flags = glyph.getCoordinates(font["glyf"])
        xs = [p[0] for p in coords] or [0]
        ys = [p[1] for p in coords] or [0]
        advance = font["hmtx"].metrics[name][0]
        return (len(end_points), min(xs), min(ys), max(xs), max(ys), len(coords), advance)
    finally:
        font.close()


def rank_candidates(font_path: Path, target_gid: int, candidates: list[str], limit: int = 12):
    target = glyph_stats(font_path, target_gid)
    rows = []
    for text in candidates:
        try:
            gids = shape(font_path, text)
        except Exception:
            gids = []
        if gids == [target_gid]:
            score = 0.0
        elif not target or not gids:
            score = 100.0
        else:
            stats = [glyph_stats(font_path, gid) for gid in gids]
            valid = [s for s in stats if s]
            if not valid:
                score = 100.0
            else:
                xmin = min(s[1] for s in valid)
                ymin = min(s[2] for s in valid)
                xmax = max(s[3] for s in valid)
                ymax = max(s[4] for s in valid)
                tw = max(1, target[3] - target[1])
                th = max(1, target[4] - target[2])
                score = abs((xmax - xmin) - tw) / tw + abs((ymax - ymin) - th) / th
                if len(gids) == 1:
                    score *= 0.75
        rows.append((score, text, gids))
    rows.sort(key=lambda row: (row[0], len(row[1]), row[1]))
    return rows[:limit]


def target_svg(font_path: Path, gid: int) -> str:
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont
    font = TTFont(str(font_path), lazy=False)
    try:
        name = font.getGlyphOrder()[gid]
        pen = SVGPathPen(font.getGlyphSet())
        font.getGlyphSet()[name].draw(pen)
        return pen.getCommands()
    finally:
        font.close()


def write_review_html(path: Path, font_path: Path, gid: int, ranked):
    encoded = base64.b64encode(font_path.read_bytes()).decode("ascii")
    path_data = target_svg(font_path, gid)
    cards = []
    for index, (score, text, gids) in enumerate(ranked, 1):
        cards.append(f'<div class="card"><div class="candidate">{index}. {text}</div><div class="meta">score={score:.6f} | shaped GIDs={gids}</div></div>')
    html = f'''<!doctype html><html><head><meta charset="utf-8"><title>EC Bangla glyph review GID {gid}</title>
<style>body{{font-family:Arial,sans-serif;margin:24px;background:#f7f7f7}}.target{{background:white;border:1px solid #bbb;padding:12px;width:340px}}.glyph{{width:320px;height:230px;border:1px solid #888;background:white}}.cards{{display:flex;flex-wrap:wrap;gap:12px;margin-top:18px}}.card{{background:white;border:1px solid #aaa;padding:16px;min-width:180px}}.candidate{{font-family:EmbeddedBangla,serif;font-size:42px}}.meta{{font-family:monospace;font-size:12px;margin-top:8px}}@font-face{{font-family:EmbeddedBangla;src:url(data:font/ttf;base64,{encoded}) format("truetype")}}</style></head><body>
<h1>PDF custom glyph — GID {gid}</h1><div class="target"><b>Actual embedded PDF glyph</b><br><svg class="glyph" viewBox="-200 -200 1600 1500"><path d="{path_data}" fill="black" transform="translate(0,1200) scale(1,-1)"/></svg></div>
<h2>Candidate renderings in the same embedded font</h2><div class="cards">{''.join(cards)}</div></body></html>'''
    path.write_text(html, encoding="utf-8")


def used_custom_gids(pdf: bytes, page: int) -> list[int]:
    objects = _object_map(pdf)
    pages = _page_objects(objects)
    page_body = pages[page - 1][1]
    used = set()
    for _content_num, _decoded, operations in _page_operations(objects, page_body):
        for operation in operations:
            values = operation.value if operation.operator == b"TJ" else (operation.value,)
            for value in values:
                if isinstance(value, bytes):
                    for pos in range(0, len(value) - 1, 2):
                        used.add(int.from_bytes(value[pos:pos + 2], "big"))
    return sorted(gid for gid in used if gid >= 120)


def run(pdf_path: Path, page: int, db_path: Path, candidate_path: Path, only_gid: int | None):
    pdf = pdf_path.read_bytes()
    db = load_database(db_path)
    legacy_path = Path("custom_glyph_map.json")
    legacy = json.loads(legacy_path.read_text(encoding="utf-8")) if legacy_path.exists() else {}
    for _resource, base_font, font_bytes in embedded_fonts(pdf, page):
        with tempfile.TemporaryDirectory(prefix="ec_pdf_font_") as temp:
            font_path = Path(temp) / "embedded.ttf"
            font_path.write_bytes(font_bytes)
            fkey = font_key(font_bytes, base_font)
            candidates = generate_candidates(candidate_path)
            candidates += ["ঁ", "ং", "ঃ", "া", "ি", "ী", "ু", "ূ", "ৃ", "ে", "ৈ", "ো", "ৌ", "্", "ৗ", "ড়", "ঢ়", "য়"]
            candidates = sorted(set(candidates), key=lambda text: (len(text), text))
            gids = used_custom_gids(pdf, page)
            if only_gid is not None:
                gids = [gid for gid in gids if gid == only_gid]
            for gid in gids:
                if str(gid) in legacy.get(base_font, {}):
                    print(f"SKIP GID {gid}: validated legacy mapping -> {legacy[base_font][str(gid)]}")
                    continue
                existing = db.get("fonts", {}).get(fkey, {}).get("glyphs", {}).get(str(gid), {})
                if existing.get("confidence", 0) >= 1.0:
                    print(f"SKIP GID {gid}: already learned -> {existing.get('text')}")
                    continue
                ranked = rank_candidates(font_path, gid, candidates)
                html_path = Path.cwd() / f"glyph_review_{gid}.html"
                write_review_html(html_path, font_path, gid, ranked)
                print(f"\nGID {gid} / CID {gid} / {base_font}")
                print(f"Visual comparison: {html_path}")
                try:
                    webbrowser.open(html_path.resolve().as_uri())
                except Exception:
                    pass
                for index, (score, text, shaped) in enumerate(ranked, 1):
                    print(f"  {index:2d}. {text}   score={score:.6f}   shaped={shaped}")
                while True:
                    answer = input("Accept [1-N], N=next, S=skip, Q=quit: ").strip().lower()
                    if answer == "q":
                        save_database(db_path, db)
                        return
                    if answer in {"n", "s"}:
                        break
                    if answer.isdigit() and 1 <= int(answer) <= len(ranked):
                        text = ranked[int(answer) - 1][1]
                        remember_mapping(db, fkey=fkey, base_font=base_font, gid=gid, gkey=glyph_key(font_path, gid), text=text, confidence=1.0)
                        save_database(db_path, db)
                        print(f"LEARNED: CID/GID {gid} -> {text} | confidence=1.0")
                        break
                    print("Enter a candidate number, N, S, or Q.")
    save_database(db_path, db)


def main() -> int:
    parser = argparse.ArgumentParser(description="Learn custom Bangla PDF CID/GID mappings interactively")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--gid", type=int, help="review only one CID/GID")
    parser.add_argument("--database", type=Path, default=Path("learned_glyph_map.json"))
    parser.add_argument("--candidates", type=Path, default=Path("data/bangla_conjuncts.json"))
    args = parser.parse_args()
    run(args.pdf, args.page, args.database, args.candidates, args.gid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

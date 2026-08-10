"""Simple human review with one self-contained HTML visual per unknown glyph."""
from __future__ import annotations

import tempfile
import webbrowser
from html import escape
from pathlib import Path

from .direct_pdf_fixed import embedded_fonts, ttf_gid_map, used_gids
from .learned_mapping import font_key, glyph_key, build_fingerprint_index, load_database, remember_mapping, save_database


def _font_line_svg(font_path: Path, cids: list[int], target_gid: int) -> str:
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont
    font = TTFont(str(font_path), lazy=False)
    try:
        order = font.getGlyphOrder(); upm = int(font["head"].unitsPerEm)
        asc = int(getattr(font["hhea"], "ascent", upm)); desc = int(getattr(font["hhea"], "descent", -upm // 4))
        x = 0.0; pieces: list[str] = []; glyph_set = font.getGlyphSet(); hmtx = font["hmtx"]
        for gid in cids:
            if gid < 0 or gid >= len(order): continue
            name = order[gid]; pen = SVGPathPen(glyph_set); glyph_set[name].draw(pen); path = pen.getCommands()
            fill = "#d11" if gid == target_gid else "#111"
            pieces.append(f'<path d="{escape(path, quote=True)}" fill="{fill}" transform="translate({x:.2f},{asc:.2f}) scale(1,-1)"/>')
            if gid == target_gid:
                advance = float(hmtx[name][0]); pieces.append(f'<line x1="{x:.2f}" y1="{asc + 70:.2f}" x2="{x + max(advance, upm * .12):.2f}" y2="{asc + 70:.2f}" stroke="#d11" stroke-width="12"/>')
            x += float(hmtx[name][0])
        width = max(x + upm * .25, upm); height = max(asc - desc + upm * .35, upm); y0 = desc - upm * .15
        return f'<svg class="line-glyphs" viewBox="0 {y0:.2f} {width:.2f} {height:.2f}" preserveAspectRatio="xMinYMid meet" role="img">' + ''.join(pieces) + '</svg>'
    finally:
        font.close()


def _write_html(path: Path, font_path: Path, gid: int, line_cids: list[int], mapping: dict[int, str] | None = None) -> None:
    """Write one HTML review file; known glyphs are decoded and only unresolved glyphs show as CIDs."""
    mapping = mapping or {}; line_svg = _font_line_svg(font_path, line_cids, gid); tokens: list[str] = []
    for cid in line_cids:
        if cid == gid: tokens.append(f'<span class="target">⟦CID:{cid}⟧</span>')
        elif cid in mapping: tokens.append(escape(mapping[cid]))
        else: tokens.append(f'<span class="unknown">⟦CID:{cid}⟧</span>')
    line_text = ''.join(tokens)
    page = f'''<!doctype html><html><head><meta charset="utf-8"><title>EC Glyph Review — CID/GID {gid}</title><style>
body{{font-family:Arial,sans-serif;margin:24px;background:#f3f4f6;color:#111}}.card{{max-width:1200px;margin:auto;background:#fff;border:1px solid #aaa;border-radius:10px;padding:22px;box-shadow:0 2px 10px #0001}}h1{{margin-top:0;font-size:24px}}.badge{{display:inline-block;background:#d11;color:#fff;font-weight:700;border-radius:5px;padding:5px 9px}}.line{{margin-top:16px;border:1px solid #888;background:#fff;padding:18px;overflow:auto}}.line-glyphs{{width:100%;height:220px;display:block;background:white}}.textline{{margin-top:12px;font-size:28px;line-height:1.7;word-break:break-all;border-top:1px solid #ddd;padding-top:12px}}.target{{color:#d11;background:#ffe0e0;border:2px solid #d11;border-radius:4px;padding:1px 4px;font-weight:700}}.unknown{{color:#b11;background:#fff0f0;border-radius:3px;padding:1px 3px}}.note{{color:#555;margin:8px 0}}.meta{{font-family:Consolas,monospace;background:#f7f7f7;padding:10px;border-radius:6px}}</style></head><body><div class="card">
<h1>EC Manual Glyph Review</h1><div class="meta">Target CID/GID: <span class="badge">{gid}</span></div>
<p class="note">The line below is rendered from the exact embedded PDF TrueType glyph outlines. Only the target CID/GID is highlighted in red.</p><div class="line">{line_svg}</div><div class="textline">{line_text}</div>
<p class="note">Known glyphs are shown as decoded Unicode. Only genuinely unresolved glyphs remain as CID markers.</p><p class="note">Enter the exact Unicode character/string in the console. The HTML file is deleted automatically after a successful save.</p>
</div></body></html>'''
    path.write_text(page, encoding="utf-8")


def run(pdf_path: Path, page: int, db_path: Path, candidate_path: Path, only_gid: int | None = None, unresolved_cids: list[int] | None = None, operations: list[dict] | None = None) -> None:
    pdf = pdf_path.read_bytes(); db = load_database(db_path); fingerprint_index = build_fingerprint_index(db)
    targets = list(unresolved_cids or [])
    if only_gid is not None: targets = [g for g in targets if g == only_gid]
    operation_lines: dict[int, list[int]] = {}
    for op in operations or []:
        cids = [int(x) for x in op.get("cids", [])]
        for cid in cids: operation_lines.setdefault(cid, cids)

    found = False
    for _resource, base_font, raw in embedded_fonts(pdf, page):
        found = True; ttf_mapping = ttf_gid_map(raw)
        gids = targets if unresolved_cids is not None else [g for g in used_gids(pdf, page) if g >= 120 and g not in ttf_mapping]
        if only_gid is not None: gids = [g for g in gids if g == only_gid]
        fkey = font_key(raw, base_font)
        with tempfile.TemporaryDirectory(prefix="ec_pdf_font_") as tmp:
            font_path = Path(tmp) / "embedded.ttf"; font_path.write_bytes(raw)
            # Start with the PDF's own ToUnicode mappings, then overlay confirmed learned mappings.
            current_map: dict[int, str] = {int(g): str(t) for g, t in ttf_mapping.items() if isinstance(t, str)}
            for entry in db.get("fonts", {}).get(fkey, {}).get("glyphs", {}).values():
                if isinstance(entry, dict) and isinstance(entry.get("gid"), int) and isinstance(entry.get("text"), str) and entry.get("confidence", 0) >= 1.0:
                    current_map[entry["gid"]] = entry["text"]

            for gid in gids:
                existing = db.get("fonts", {}).get(fkey, {}).get("glyphs", {}).get(str(gid), {})
                if existing.get("confidence", 0) >= 1.0:
                    print(f'SKIP GID {gid}: already learned -> {existing.get("text")}', flush=True); continue
                gkey = glyph_key(font_path, gid); reused = fingerprint_index.get(gkey)
                if reused is not None:
                    text = str(reused["text"]); remember_mapping(db, fkey=fkey, base_font=base_font, gid=gid, gkey=gkey, text=text, source="glyph_fingerprint_reuse", confidence=1.0)
                    current_map[gid] = text; fingerprint_index[gkey] = db["fonts"][fkey]["glyphs"][str(gid)]; save_database(db_path, db)
                    print(f'AUTO-REUSED GID {gid} by glyph fingerprint -> {text}', flush=True); continue

                html_path = Path.cwd() / f"glyph_review_{gid}.html"; line_cids = operation_lines.get(gid, [gid])

                # Resolve every other CID in the review line by the global fingerprint index
                # before rendering the HTML. This prevents a previously learned glyph such as
                # CID 207 from appearing as an unnecessary CID marker just because it is also
                # present in the same operation line as the genuinely unknown target.
                for line_cid in line_cids:
                    if line_cid == gid or line_cid in current_map:
                        continue
                    if line_cid < 0 or line_cid >= len(font_path.read_bytes()):
                        continue
                    line_gkey = glyph_key(font_path, line_cid)
                    line_reused = fingerprint_index.get(line_gkey)
                    if line_reused is not None:
                        line_text = str(line_reused["text"])
                        remember_mapping(db, fkey=fkey, base_font=base_font, gid=line_cid, gkey=line_gkey, text=line_text, source="glyph_fingerprint_reuse", confidence=1.0)
                        current_map[line_cid] = line_text
                        fingerprint_index[line_gkey] = db["fonts"][fkey]["glyphs"][str(line_cid)]
                        print(f'AUTO-REUSED LINE GID {line_cid} by glyph fingerprint -> {line_text}', flush=True)
                save_database(db_path, db)

                _write_html(html_path, font_path, gid, line_cids, current_map); print(f"HTML: {html_path.resolve()}", flush=True)
                try: webbrowser.open(html_path.resolve().as_uri(), new=2)
                except Exception as exc: print(f"Could not automatically open browser: {exc}", flush=True)
                print("\n" + "=" * 72, flush=True); print(f"GLYPH REVIEW — CID/GID {gid} — font {base_font}", flush=True); print("=" * 72, flush=True)
                print("The HTML shows the complete text line and highlights this exact CID/GID in red.", flush=True); print(f"HTML: {html_path.resolve()}", flush=True)
                saved = False
                while True:
                    answer = input("M=manual Unicode, N=next, S=skip, Q=quit: ").strip().lower()
                    if answer == "q": save_database(db_path, db); return
                    if answer in {"n", "s"}: break
                    if answer == "m":
                        text = input("Enter exact Unicode character/string: ").strip()
                        if not text: print("Empty mapping not accepted.", flush=True); continue
                        remember_mapping(db, fkey=fkey, base_font=base_font, gid=gid, gkey=gkey, text=text, source="user_confirmed", confidence=1.0)
                        current_map[gid] = text; fingerprint_index[gkey] = db["fonts"][fkey]["glyphs"][str(gid)]; save_database(db_path, db)
                        print(f"LEARNED: CID/GID {gid} -> {text} | confidence=1.0", flush=True); saved = True; break
                    print("Enter M, N, S, or Q.", flush=True)
                if saved:
                    try: html_path.unlink(missing_ok=True); print(f"DELETED: {html_path.name}", flush=True)
                    except OSError as exc: print(f"WARNING: could not delete {html_path}: {exc}", flush=True)
    if not found: raise RuntimeError(f"Glyph review found no embedded font on page {page}")
    save_database(db_path, db)

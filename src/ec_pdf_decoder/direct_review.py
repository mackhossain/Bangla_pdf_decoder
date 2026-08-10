"""Simple human review using the actual embedded PDF glyph visualization."""
from __future__ import annotations
import tempfile, webbrowser
from pathlib import Path
from .direct_pdf_fixed import embedded_fonts, ttf_gid_map, used_gids
from .learned_mapping import font_key, glyph_key, find_by_glyph_fingerprint, load_database, remember_mapping, save_database


def _svg(font_path: Path, gid: int) -> str:
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont
    font = TTFont(str(font_path), lazy=False)
    try:
        name = font.getGlyphOrder()[gid]
        pen = SVGPathPen(font.getGlyphSet()); font.getGlyphSet()[name].draw(pen)
        return pen.getCommands()
    finally: font.close()


def _write_actual_svg(path: Path, font_path: Path, gid: int) -> None:
    target = _svg(font_path, gid)
    path.write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="900" viewBox="-250 -200 1700 1500"><rect width="100%" height="100%" fill="white"/><path d="{target}" fill="black" transform="translate(0,1200) scale(1,-1)"/></svg>''', encoding="utf-8")


def _write_html(path: Path, font_path: Path, gid: int) -> None:
    target = _svg(font_path, gid)
    page = f'''<!doctype html><html><head><meta charset="utf-8"><title>EC glyph {gid}</title><style>body{{font-family:Arial;margin:24px;background:#f3f4f6;color:#111}}.target{{max-width:900px;background:#fff;border:1px solid #aaa;border-radius:8px;padding:18px}}.glyph{{width:100%;height:650px;border:1px solid #888;background:#fff}}.note{{padding:10px 0;color:#444}}</style></head><body><h1>EC Glyph Review — CID/GID {gid}</h1><div class="target"><h2>Actual PDF glyph</h2><div class="note">This is the exact outline from the embedded Bangla TrueType font. No candidate suggestions are shown.</div><svg class="glyph" viewBox="-250 -200 1700 1500"><path d="{target}" fill="black" transform="translate(0,1200) scale(1,-1)"/></svg></div></body></html>'''
    path.write_text(page, encoding='utf-8')


def run(pdf_path: Path, page: int, db_path: Path, candidate_path: Path, only_gid: int | None = None, unresolved_cids: list[int] | None = None) -> None:
    """Review only genuinely unknown glyphs; reuse identical confirmed outlines automatically."""
    pdf = pdf_path.read_bytes(); db = load_database(db_path); targets = list(unresolved_cids or [])
    if only_gid is not None: targets = [g for g in targets if g == only_gid]
    found = False
    for _resource, base_font, raw in embedded_fonts(pdf, page):
        found = True; cmap = set(ttf_gid_map(raw)); gids = targets if unresolved_cids is not None else [g for g in used_gids(pdf, page) if g >= 120 and g not in cmap]
        if only_gid is not None: gids = [g for g in gids if g == only_gid]
        fkey = font_key(raw, base_font)
        with tempfile.TemporaryDirectory(prefix='ec_pdf_font_') as tmp:
            font_path = Path(tmp) / 'embedded.ttf'; font_path.write_bytes(raw)
            for gid in gids:
                existing = db.get('fonts', {}).get(fkey, {}).get('glyphs', {}).get(str(gid), {})
                if existing.get('confidence', 0) >= 1.0:
                    print(f'SKIP GID {gid}: already learned -> {existing.get("text")}', flush=True); continue

                gkey = glyph_key(font_path, gid)
                reused = find_by_glyph_fingerprint(db, gkey)
                if reused is not None:
                    remember_mapping(db, fkey=fkey, base_font=base_font, gid=gid, gkey=gkey, text=str(reused['text']), source='glyph_fingerprint_reuse', confidence=1.0)
                    save_database(db_path, db)
                    print(f'AUTO-REUSED GID {gid} by glyph fingerprint -> {reused["text"]}', flush=True)
                    continue

                print(f'Preparing actual PDF glyph visual for CID/GID {gid}...', flush=True)
                html_path = Path.cwd() / f'glyph_review_{gid}.html'; svg_path = Path.cwd() / f'actual_glyph_{gid}.svg'
                _write_actual_svg(svg_path, font_path, gid); _write_html(html_path, font_path, gid)
                print(f'  SVG:  {svg_path.resolve()}', flush=True); print(f'  HTML: {html_path.resolve()}', flush=True)
                try: webbrowser.open(html_path.resolve().as_uri(), new=2)
                except Exception as exc: print(f'Could not automatically open browser: {exc}', flush=True)
                print('\n' + '=' * 72, flush=True); print(f'GLYPH REVIEW — CID/GID {gid} — font {base_font}', flush=True); print('=' * 72, flush=True)
                print('ACTUAL PDF GLYPH:', flush=True); print(f'  SVG:  {svg_path.resolve()}', flush=True); print(f'  HTML: {html_path.resolve()}', flush=True); print('Look at the glyph in the browser, then enter the exact Unicode text.', flush=True)
                while True:
                    answer = input('M=manual Unicode, N=next, S=skip, Q=quit: ').strip().lower()
                    if answer == 'q': save_database(db_path, db); return
                    if answer in {'n', 's'}: break
                    if answer == 'm':
                        text = input('Enter exact Unicode character/string: ').strip()
                        if not text: print('Empty mapping not accepted.', flush=True); continue
                        remember_mapping(db, fkey=fkey, base_font=base_font, gid=gid, gkey=gkey, text=text, source='user_confirmed', confidence=1.0); save_database(db_path, db)
                        print(f'LEARNED: CID/GID {gid} -> {text} | confidence=1.0', flush=True); break
                    print('Enter M, N, S, or Q.', flush=True)
    if not found: raise RuntimeError(f'Glyph review found no embedded font on page {page}')
    save_database(db_path, db)

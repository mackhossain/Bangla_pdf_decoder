"""Fast interactive review for unresolved EC Bangla glyphs."""
from __future__ import annotations
import base64, html, tempfile, webbrowser
from pathlib import Path
from .bangla_candidates import generate_candidates
from .direct_pdf_fixed import embedded_fonts, ttf_gid_map, used_gids
from .learned_mapping import font_key, glyph_key, load_database, remember_mapping, save_database

def _shape(font_path: Path, text: str):
    import uharfbuzz as hb
    blob=hb.Blob.from_file_path(str(font_path)); face=hb.Face(blob); font=hb.Font(face)
    buf=hb.Buffer(); buf.add_str(text); buf.guess_segment_properties(); hb.shape(font,buf)
    return [i.codepoint for i in buf.glyph_infos]

def _stats(font_path: Path, gid: int):
    from fontTools.ttLib import TTFont
    font=TTFont(str(font_path),lazy=False)
    try:
        order=font.getGlyphOrder()
        if not 0<=gid<len(order): return None
        name=order[gid]; glyph=font["glyf"][name]; coords,endpoints,_=glyph.getCoordinates(font["glyf"])
        xs=[p[0] for p in coords] or [0]; ys=[p[1] for p in coords] or [0]
        return (len(endpoints),min(xs),min(ys),max(xs),max(ys),len(coords),font["hmtx"].metrics[name][0])
    finally: font.close()

def rank(font_path: Path, target_gid: int, candidates: list[str], limit: int=12):
    target=_stats(font_path,target_gid); rows=[]
    for text in candidates:
        try: gids=_shape(font_path,text)
        except Exception: gids=[]
        if gids==[target_gid]: score=0.0
        elif not target or not gids: score=100.0
        else:
            stats=[_stats(font_path,g) for g in gids]; stats=[s for s in stats if s]
            if not stats: score=100.0
            else:
                xmin=min(s[1] for s in stats); ymin=min(s[2] for s in stats); xmax=max(s[3] for s in stats); ymax=max(s[4] for s in stats)
                tw=max(1,target[3]-target[1]); th=max(1,target[4]-target[2])
                score=abs((xmax-xmin)-tw)/tw+abs((ymax-ymin)-th)/th
                if len(gids)==1: score*=0.75
        rows.append((score,text,gids))
    rows.sort(key=lambda x:(x[0],len(x[1]),x[1])); return rows[:limit]

def _svg(font_path: Path,gid:int)->str:
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont
    font=TTFont(str(font_path),lazy=False)
    try:
        name=font.getGlyphOrder()[gid]; pen=SVGPathPen(font.getGlyphSet()); font.getGlyphSet()[name].draw(pen); return pen.getCommands()
    finally: font.close()

def _write_html(path:Path,font_path:Path,gid:int,ranked):
    encoded=base64.b64encode(font_path.read_bytes()).decode("ascii"); target=_svg(font_path,gid)
    cards=''.join(f'<div class="card"><div class="candidate">{html.escape(t)}</div><div class="meta">{i}. score={s:.6f} shaped={g}</div></div>' for i,(s,t,g) in enumerate(ranked,1))
    page=f'''<!doctype html><html><head><meta charset="utf-8"><title>EC glyph {gid}</title><style>body{{font-family:Arial;margin:24px;background:#f7f7f7}}.target,.card{{background:#fff;border:1px solid #aaa;padding:14px}}.target{{width:360px}}.glyph{{width:340px;height:250px;border:1px solid #888;background:#fff}}.cards{{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}}.card{{min-width:190px}}.candidate{{font-family:EmbeddedBangla,serif;font-size:44px}}.meta{{font:12px monospace;margin-top:8px}}@font-face{{font-family:EmbeddedBangla;src:url(data:font/ttf;base64,{encoded}) format("truetype")}}</style></head><body><h1>EC custom glyph — GID/CID {gid}</h1><div class="target"><b>Actual PDF glyph</b><br><svg class="glyph" viewBox="-250 -200 1700 1500"><path d="{target}" fill="black" transform="translate(0,1200) scale(1,-1)"/></svg></div><h2>Candidate Unicode strings</h2><div class="cards">{cards}</div></body></html>'''
    path.write_text(page,encoding='utf-8')

def run(pdf_path:Path,page:int,db_path:Path,candidate_path:Path,only_gid:int|None=None,unresolved_cids:list[int]|None=None):
    """Interactive review using the decoder's authoritative unresolved list.

    Candidate generation is deliberately curated-only. The old full pair/triple
    generator caused a long CPU-bound ranking pass before input() was reached.
    """
    pdf=pdf_path.read_bytes(); db=load_database(db_path)
    candidates=generate_candidates(candidate_path,include_generated=False)
    candidates += ["ঁ","ং","ঃ","া","ি","ী","ু","ূ","ৃ","ে","ৈ","ো","ৌ","্","ৗ","ড়","ঢ়","য়"]
    candidates=sorted(set(candidates),key=lambda x:(len(x),x))
    targets=list(unresolved_cids or [])
    if only_gid is not None: targets=[g for g in targets if g==only_gid]
    for _resource,base_font,raw in embedded_fonts(pdf,page):
        cmap=set(ttf_gid_map(raw))
        gids=targets if unresolved_cids is not None else [g for g in used_gids(pdf,page) if g>=120 and g not in cmap]
        if only_gid is not None: gids=[g for g in gids if g==only_gid]
        fkey=font_key(raw,base_font)
        with tempfile.TemporaryDirectory(prefix='ec_pdf_font_') as tmp:
            font_path=Path(tmp)/'embedded.ttf'; font_path.write_bytes(raw)
            for gid in gids:
                existing=db.get('fonts',{}).get(fkey,{}).get('glyphs',{}).get(str(gid),{})
                if existing.get('confidence',0)>=1.0:
                    print(f'SKIP GID {gid}: already learned -> {existing.get("text")}',flush=True); continue
                print(f'Preparing review for CID/GID {gid}...',flush=True)
                ranked=rank(font_path,gid,candidates)
                review_html=Path.cwd()/f'glyph_review_{gid}.html'; _write_html(review_html,font_path,gid,ranked)
                print('\n'+'='*64,flush=True); print(f'GLYPH REVIEW — CID/GID {gid} — font {base_font}',flush=True); print('='*64,flush=True)
                print(f'Visual comparison: {review_html}',flush=True)
                try: webbrowser.open(review_html.resolve().as_uri())
                except Exception: pass
                for i,(score,text,shaped) in enumerate(ranked,1): print(f'  {i:2d}. {text}   score={score:.6f}   shaped={shaped}',flush=True)
                while True:
                    answer=input('Accept [1-N], M=manual Unicode, N=next, S=skip, Q=quit: ').strip().lower()
                    if answer=='q': save_database(db_path,db); return
                    if answer in {'n','s'}: break
                    if answer=='m':
                        text=input('Enter exact Unicode character/string: ').strip()
                        if not text: print('Empty mapping not accepted.',flush=True); continue
                        remember_mapping(db,fkey=fkey,base_font=base_font,gid=gid,gkey=glyph_key(font_path,gid),text=text,source='user_confirmed',confidence=1.0); save_database(db_path,db); print(f'LEARNED: CID/GID {gid} -> {text} | confidence=1.0',flush=True); break
                    if answer.isdigit() and 1<=int(answer)<=len(ranked):
                        text=ranked[int(answer)-1][1]; remember_mapping(db,fkey=fkey,base_font=base_font,gid=gid,gkey=glyph_key(font_path,gid),text=text,source='user_confirmed',confidence=1.0); save_database(db_path,db); print(f'LEARNED: CID/GID {gid} -> {text} | confidence=1.0',flush=True); break
                    print('Enter a candidate number, M, N, S, or Q.',flush=True)
    save_database(db_path,db)

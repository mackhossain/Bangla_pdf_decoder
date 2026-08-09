"""Interactive glyph review with optional AI assistance."""
from __future__ import annotations
import base64,json,os,tempfile
from pathlib import Path
from typing import Any
from .bangla_candidates import generate_candidates
from .direct_pdf_fixed import embedded_fonts,ttf_gid_map,used_gids
from .learned_mapping import font_key,glyph_key,load_database,remember_mapping,save_database
from .direct_review import _svg,rank

def _glyph_png(font_path:Path,gid:int)->bytes:
    import cairosvg
    path=_svg(font_path,gid)
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="700" viewBox="-250 -250 2200 1800"><rect width="100%" height="100%" fill="white"/><path d="{path}" fill="black" transform="translate(0,1400) scale(1,-1)"/></svg>'
    return cairosvg.svg2png(bytestring=svg.encode(),output_width=900,output_height=700)

def _ask_ai(image:bytes,*,gid:int,base_font:str,candidates:list[str],ranked):
    key=os.getenv('OPENAI_API_KEY')
    if not key: raise RuntimeError('OPENAI_API_KEY is not set')
    from openai import OpenAI
    model=os.getenv('OPENAI_MODEL','gpt-5.4-mini')
    candidate_text='\n'.join(f'- {c}' for c in candidates)
    deterministic='\n'.join(f'- {text!r}: shape_score={score:.6f}, shaped_gids={gids}' for score,text,gids in ranked[:20])
    prompt=f'''Identify this ONE Bengali glyph from an embedded Bangladesh Election Commission TrueType font. CID/GID={gid}, font={base_font!r}. Choose only from the supplied candidates. Prefer glyph shape and deterministic font/HarfBuzz evidence. Return ONLY JSON: {{"candidates":[{{"text":"...","confidence":0.0,"reason":"..."}}]}}. At most 5 candidates, confidence 0..1.\n\nCandidates:\n{candidate_text}\n\nDeterministic candidates:\n{deterministic}'''
    data_url='data:image/png;base64,'+base64.b64encode(image).decode('ascii')
    response=OpenAI(api_key=key).responses.create(model=model,input=[{'role':'user','content':[{'type':'input_text','text':prompt},{'type':'input_image','image_url':data_url}]}])
    parsed=json.loads(response.output_text.strip()); allowed=set(candidates); result=[]
    for row in parsed.get('candidates',[]) if isinstance(parsed,dict) else []:
        if not isinstance(row,dict) or row.get('text') not in allowed: continue
        try: conf=max(0.0,min(1.0,float(row.get('confidence',0))))
        except (TypeError,ValueError): conf=0.0
        result.append({'text':row['text'],'confidence':conf,'reason':str(row.get('reason',''))})
    return sorted(result,key=lambda x:x['confidence'],reverse=True)[:5]

def run(pdf_path:Path,page:int,db_path:Path,candidate_path:Path,only_gid:int|None=None,unresolved_cids:list[int]|None=None,use_ai:bool=True)->None:
    pdf=pdf_path.read_bytes(); db=load_database(db_path)
    curated=generate_candidates(candidate_path,include_generated=False)+['ঁ','ং','ঃ','া','ি','ী','ু','ূ','ৃ','ে','ৈ','ো','ৌ','্','ৗ','ড়','ঢ়','য়']; curated=sorted(set(curated),key=lambda x:(len(x),x)); all_candidates=curated
    targets=list(unresolved_cids or [])
    if only_gid is not None: targets=[g for g in targets if g==only_gid]
    found=False
    for _resource,base_font,raw in embedded_fonts(pdf,page):
        found=True; cmap=set(ttf_gid_map(raw))
        if unresolved_cids is None: targets=[g for g in used_gids(pdf,page) if g>=120 and g not in cmap]
        if only_gid is not None: targets=[g for g in targets if g==only_gid]
        fkey=font_key(raw,base_font)
        with tempfile.TemporaryDirectory(prefix='ec_pdf_review_') as tmp:
            font_path=Path(tmp)/'embedded.ttf'; font_path.write_bytes(raw)
            for gid in targets:
                existing=db.get('fonts',{}).get(fkey,{}).get('glyphs',{}).get(str(gid),{})
                if existing.get('confidence',0)>=1.0: continue
                ranked=rank(font_path,gid,all_candidates)
                options=[]
                if use_ai:
                    try:
                        ai_rows=_ask_ai(_glyph_png(font_path,gid),gid=gid,base_font=base_font,candidates=sorted(set(curated+[r[1] for r in ranked[:100]])),ranked=ranked)
                    except Exception as exc:
                        print(f'AI REVIEW UNAVAILABLE: {exc}'); ai_rows=[]
                else: ai_rows=[]
                seen=set()
                for row in ai_rows:
                    if row['text'] not in seen: options.append((row['text'],row['confidence'],'AI')); seen.add(row['text'])
                for score,text,_gids in ranked:
                    if text not in seen: options.append((text,max(0.0,1.0-min(1.0,score)),'font')); seen.add(text)
                    if len(options)>=10: break
                print('\n'+'='*64); print(f'GLYPH REVIEW — CID/GID {gid} — font {base_font}'); print('='*64)
                if use_ai: print('AI is advisory only; YOU must confirm.');
                for i,(text,conf,source) in enumerate(options[:10],1): print(f'  {i:2d}. {text}   confidence={conf:.3f}   [{source}]')
                while True:
                    answer=input('Accept [1-N], M=manual Unicode, N=next, S=skip, Q=quit: ').strip().lower()
                    if answer=='q': save_database(db_path,db); return
                    if answer in {'n','s'}: break
                    if answer=='m':
                        text=input('Enter exact Unicode character/string: ')
                        if not text: print('Empty mapping not accepted.'); continue
                    elif answer.isdigit() and 1<=int(answer)<=len(options): text=options[int(answer)-1][0]
                    else: print('Enter a candidate number, M, N, S, or Q.'); continue
                    remember_mapping(db,fkey=fkey,base_font=base_font,gid=gid,gkey=glyph_key(font_path,gid),text=text,source='user_confirmed_ai' if answer.isdigit() and options[int(answer)-1][2]=='AI' else 'user_confirmed',confidence=1.0); save_database(db_path,db); print(f'LEARNED: CID/GID {gid} -> {text} | confidence=1.0'); break
    if not found: raise RuntimeError(f'Glyph review found no embedded font on page {page}')
    save_database(db_path,db)

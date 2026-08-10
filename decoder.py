"""EC Bangla PDF decoder with embedded-TTF fallback and interactive learning."""
from __future__ import annotations
import argparse,json,os,tempfile,time
from functools import lru_cache
from pathlib import Path
from src.ec_pdf_decoder.bangla_cleanup import cleanup_bangla_text
from src.ec_pdf_decoder.bangla_harfbuzz import choose_candidate
from src.ec_pdf_decoder.bangla_order import restore_bangla_logical_order_tokens
from src.ec_pdf_decoder.direct_pdf_fixed import analyze_page_direct,embedded_fonts,ttf_gid_map
from src.ec_pdf_decoder.direct_pdf import read_pdf_bytes
from src.ec_pdf_decoder.learned_mapping import font_key,glyph_fingerprint_map,build_fingerprint_index,load_database
from src.ec_pdf_decoder.direct_review import run as review_run
from src.ec_pdf_decoder.unicode_ttf import generate as generate_unicode_ttf

@lru_cache(maxsize=4)
def _cached_learned_database(path_str:str,mtime_ns:int,size:int):
    return load_database(Path(path_str))

def _load_learned_database(path:Path):
    try: stat=path.stat()
    except FileNotFoundError: return load_database(path)
    return _cached_learned_database(str(path.resolve()),stat.st_mtime_ns,stat.st_size)

def _clear_learned_cache(): _cached_learned_database.cache_clear()

@lru_cache(maxsize=8)
def _cached_fingerprint_map(font_id:str,raw:bytes):
    """Compute a font's glyph fingerprints once per embedded-font byte identity."""
    return glyph_fingerprint_map(raw)

def _cache_embedded_ttf(pdf:bytes,page:int)->Path|None:
    cache_dir=Path('data/embedded_fonts'); cache_dir.mkdir(parents=True,exist_ok=True)
    existing=sorted(cache_dir.glob('*.ttf'))
    if existing: return existing[0]
    for _resource,base_font,raw in embedded_fonts(pdf,page):
        if raw:
            safe=''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in base_font) or 'Bangla'; out=cache_dir/f'{safe}.ttf'; out.write_bytes(raw); return out
    return None

def _materialize_mapping(pdf:bytes,page:int,legacy_path:Path,learned_path:Path,learned=None)->Path:
    base=json.loads(legacy_path.read_text(encoding='utf-8')) if legacy_path.exists() else {}
    learned=learned if learned is not None else _load_learned_database(learned_path)
    fingerprint_index=build_fingerprint_index(learned); merged={k:dict(v) for k,v in base.items()}
    for _resource,base_font,raw in embedded_fonts(pdf,page):
        section=merged.setdefault(base_font,{})
        for gid,text in ttf_gid_map(raw).items(): section.setdefault(str(gid),text)
        glyphs=learned.get('fonts',{}).get(font_key(raw,base_font),{}).get('glyphs',{})
        for gid,entry in glyphs.items():
            if isinstance(entry,dict) and entry.get('confidence',0)>=1.0: section[str(gid)]=str(entry.get('text',''))
        # The old implementation opened the same TTF once per GID. For a 200+ glyph
        # font that meant hundreds of TTFont parses on every page. Cache the complete
        # fingerprint map by the exact embedded-font bytes instead.
        for gid,gkey in _cached_fingerprint_map(font_key(raw,base_font),raw).items():
            key=str(gid)
            if key in section: continue
            match=fingerprint_index.get(gkey)
            if match is not None: section[key]=str(match.get('text',''))
    fd,name=tempfile.mkstemp(prefix='ec_mapping_',suffix='.json'); os.close(fd); path=Path(name); path.write_text(json.dumps(merged,ensure_ascii=False,indent=2),encoding='utf-8'); return path

def _flat_mapping(mapping_path:Path)->dict[str,str]:
    try: data=json.loads(mapping_path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError): return {}
    flat={}
    if not isinstance(data,dict): return flat
    for section in data.values():
        if not isinstance(section,dict): continue
        for key,value in section.items():
            if isinstance(value,str): flat[str(key)]=value
            elif isinstance(value,dict) and isinstance(value.get('text'),str): flat[str(key)]=value['text']
    return flat

def _logical_text(report:dict,mapping_path:Path,font_bytes:bytes|None=None,corrections_path:Path|None=None)->str:
    mapping=_flat_mapping(mapping_path); chunks=[]
    for operation in report.get('operations',[]):
        cids=[int(value) for value in operation.get('cids',[])]; tokens=[mapping.get(str(cid),f'⟦CID:{cid}⟧') for cid in cids]; original=''.join(tokens); reordered=''.join(restore_bangla_logical_order_tokens(tokens,visual_order=True))
        if any(token.startswith('⟦CID:') for token in tokens): selected=reordered
        else: selected,_score=choose_candidate(font_bytes,cids,original,reordered)
        chunks.append(selected)
    return cleanup_bangla_text(__import__('unicodedata').normalize('NFC','\n'.join(chunks)),corrections_path)

def decode_pdf(pdf_path:str|Path,page:int,review:bool=False,*,mapping_path:str|Path='custom_glyph_map.json',learned_path:str|Path='learned_glyph_map.json',corrections_path:str|Path='data/bangla_corrections.json',gid:int|None=None,return_report:bool=False,learned_db=None,profile:bool=False):
    """Decode one PDF page. Set profile=True to print stage timings; decoding behavior is unchanged."""
    pdf_path=Path(pdf_path); mapping_path=Path(mapping_path); learned_path=Path(learned_path); corrections_path=Path(corrections_path)
    timings={}; t0=time.perf_counter()
    learned_db=learned_db if learned_db is not None else _load_learned_database(learned_path); timings['learned_map_load']=time.perf_counter()-t0
    t=time.perf_counter(); pdf=read_pdf_bytes(pdf_path); timings['pdf_read']=time.perf_counter()-t
    t=time.perf_counter(); _cache_embedded_ttf(pdf,page); timings['font_cache']=time.perf_counter()-t
    t=time.perf_counter(); materialized=_materialize_mapping(pdf,page,mapping_path,learned_path,learned_db); timings['mapping_materialize']=time.perf_counter()-t
    report={'missing_cids':[]}
    try:
        t=time.perf_counter(); report=analyze_page_direct(pdf_path,page,materialized); timings['page_analysis']=time.perf_counter()-t
        if review and report['missing_cids']:
            t=time.perf_counter(); review_run(pdf_path,page,learned_path,Path('data/bangla_conjuncts.json'),gid,list(report['missing_cids']),report.get('operations',[])); timings['manual_review']=time.perf_counter()-t
            _clear_learned_cache(); learned_db=_load_learned_database(learned_path)
            try: materialized.unlink(missing_ok=True)
            except PermissionError: pass
            t=time.perf_counter(); materialized=_materialize_mapping(pdf,page,mapping_path,learned_path,learned_db); timings['mapping_rematerialize']=time.perf_counter()-t
            t=time.perf_counter(); report=analyze_page_direct(pdf_path,page,materialized); timings['page_reanalysis']=time.perf_counter()-t
        t=time.perf_counter(); font_bytes=next((raw for _resource,_base,raw in embedded_fonts(pdf,page)),None); timings['font_bytes_lookup']=time.perf_counter()-t
        t=time.perf_counter(); text=_logical_text(report,materialized,font_bytes,corrections_path); timings['text_processing']=time.perf_counter()-t
        timings['total']=sum(timings.values())
        if profile:
            print('PROFILE — PAGE',page); print('─'*42)
            for name,value in timings.items(): print(f'{name.replace("_"," ").title():24s} {value:7.3f} s')
            print('─'*42)
        return (text,report) if return_report else text
    finally:
        try: materialized.unlink(missing_ok=True)
        except PermissionError: pass

def main()->int:
    parser=argparse.ArgumentParser(description='Decode Bangladesh EC Bangla PDF text')
    parser.add_argument('pdf',type=Path,nargs='?',help='PDF input (not needed with --generate-ttf)'); parser.add_argument('--page',type=int,required=False); parser.add_argument('--all-pages',action='store_true',help='decode every PDF page sequentially')
    parser.add_argument('--mapping',type=Path,default=Path('custom_glyph_map.json')); parser.add_argument('--learned',type=Path,default=Path('learned_glyph_map.json')); parser.add_argument('--corrections',type=Path,default=Path('data/bangla_corrections.json'))
    parser.add_argument('--review',action='store_true'); parser.add_argument('--profile',action='store_true',help='print timing for each decoder stage; does not change decoding'); parser.add_argument('--gid',type=int); parser.add_argument('--text',type=Path); parser.add_argument('--json',dest='json_path',type=Path); parser.add_argument('--generate-ttf',action='store_true',help='generate a Unicode TTF from confirmed learned glyph mappings; no PDF/page required')
    args=parser.parse_args()
    if args.generate_ttf:
        try: output,stats=generate_unicode_ttf()
        except Exception as exc: print(f'TTF GENERATION FAILED: {exc}'); return 1
        print('GENERATING UNICODE TTF'); print('======================'); print(f'Source glyph mappings : {stats["source_glyph_mappings"]}'); print(f'Single Unicode maps   : {stats["single_unicode_mappings"]}'); print(f'OpenType ligatures    : {stats["ligature_mappings"]}'); print(f'TTF: {output}'); return 0
    if args.pdf is None: parser.error('the following arguments are required: pdf (unless --generate-ttf is used)')
    if args.all_pages and args.page is not None: parser.error('--page and --all-pages cannot be used together')
    if not args.all_pages and args.page is None: parser.error('--page is required unless --all-pages or --generate-ttf is used')
    if args.all_pages:
        try:
            from pypdf import PdfReader
            page_count=len(PdfReader(str(args.pdf)).pages)
        except Exception as exc:
            print(f'DECODE FAILED: could not determine page count: {exc}'); return 1
        all_text=[]; any_unresolved=False; learned_db=_load_learned_database(args.learned)
        for page in range(1,page_count+1):
            print(f'\n===== PAGE {page}/{page_count} =====',flush=True)
            try:
                text,report=decode_pdf(args.pdf,page,args.review,mapping_path=args.mapping,learned_path=args.learned,corrections_path=args.corrections,gid=args.gid,return_report=True,learned_db=learned_db,profile=args.profile)
                all_text.append(text)
                if args.review: learned_db=_load_learned_database(args.learned)
                missing=report.get('missing_cids',[]); any_unresolved |= bool(missing)
                print(f'PAGE: {page} | MAPPED ENTRIES: {report.get("tounicode_entries",0)} | UNRESOLVED CIDs: {missing}',flush=True)
                print(text,flush=True)
            except Exception as exc:
                print(f'PAGE {page} FAILED: {exc}',flush=True); any_unresolved=True; all_text.append('')
        combined='\n'.join(all_text)
        if args.text: args.text.write_text(combined+'\n',encoding='utf-8')
        if args.json_path: args.json_path.write_text(json.dumps({'pdf':args.pdf.name,'pages':page_count},ensure_ascii=False,indent=2),encoding='utf-8')
        return 2 if any_unresolved else 0
    try: text,report=decode_pdf(args.pdf,args.page,args.review,mapping_path=args.mapping,learned_path=args.learned,corrections_path=args.corrections,gid=args.gid,return_report=True,profile=args.profile)
    except Exception as exc: print(f'DECODE FAILED: {exc}'); return 1
    print(f'PDF: {args.pdf.name}'); print(f'PAGE: {args.page}'); print(f'MAPPED ENTRIES: {report["tounicode_entries"]}'); print(f'UNRESOLVED CIDs: {report["missing_cids"]}'); print('\n# DECODED TEXT'); print('='*72); print(text)
    if args.text: args.text.write_text(text+'\n',encoding='utf-8')
    if args.json_path: args.json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if not report['missing_cids'] else 2

if __name__=='__main__': raise SystemExit(main())
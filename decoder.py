"""EC Bangla PDF decoder with embedded-TTF fallback and interactive learning."""
from __future__ import annotations
import argparse,json,os,tempfile
from pathlib import Path
from src.ec_pdf_decoder.bangla_cleanup import cleanup_bangla_text
from src.ec_pdf_decoder.bangla_harfbuzz import choose_candidate
from src.ec_pdf_decoder.bangla_order import restore_bangla_logical_order_tokens
from src.ec_pdf_decoder.direct_pdf_fixed import analyze_page_direct,embedded_fonts,ttf_gid_map
from src.ec_pdf_decoder.learned_mapping import font_key,glyph_key,find_by_glyph_fingerprint,load_database
from src.ec_pdf_decoder.direct_review import run as review_run
from src.ec_pdf_decoder.unicode_ttf import generate as generate_unicode_ttf


def _cache_embedded_ttf(pdf:bytes,page:int)->Path|None:
    cache_dir=Path('data/embedded_fonts'); cache_dir.mkdir(parents=True,exist_ok=True)
    existing=sorted(cache_dir.glob('*.ttf'))
    if existing: return existing[0]
    for _resource,base_font,raw in embedded_fonts(pdf,page):
        if raw:
            safe=''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in base_font) or 'Bangla'; out=cache_dir/f'{safe}.ttf'; out.write_bytes(raw); return out
    return None


def _materialize_mapping(pdf:bytes,page:int,legacy_path:Path,learned_path:Path)->Path:
    base=json.loads(legacy_path.read_text(encoding='utf-8')) if legacy_path.exists() else {}
    learned=load_database(learned_path); merged={k:dict(v) for k,v in base.items()}
    for _resource,base_font,raw in embedded_fonts(pdf,page):
        section=merged.setdefault(base_font,{})
        for gid,text in ttf_gid_map(raw).items(): section.setdefault(str(gid),text)
        fd,font_name=tempfile.mkstemp(prefix='ec_font_',suffix='.ttf'); os.close(fd); font_path=Path(font_name); font_path.write_bytes(raw)
        try:
            glyphs=learned.get('fonts',{}).get(font_key(raw,base_font),{}).get('glyphs',{})
            for gid,entry in glyphs.items():
                if isinstance(entry,dict) and entry.get('confidence',0)>=1.0: section[str(gid)]=str(entry.get('text',''))
            font=None
            try:
                from fontTools.ttLib import TTFont
                font=TTFont(str(font_path),lazy=False)
                glyph_count=len(font.getGlyphOrder())
                for gid in range(glyph_count):
                    key=str(gid)
                    if key in section: continue
                    try: gkey=glyph_key(font_path,gid); match=find_by_glyph_fingerprint(learned,gkey)
                    except Exception: continue
                    if match is not None: section[key]=str(match['text'])
            finally:
                if font is not None: font.close()
        finally: font_path.unlink(missing_ok=True)
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


def main()->int:
    parser=argparse.ArgumentParser(description='Decode Bangladesh EC Bangla PDF text')
    parser.add_argument('pdf',type=Path,nargs='?',help='PDF input (not needed with --generate-ttf)'); parser.add_argument('--page',type=int,required=False)
    parser.add_argument('--mapping',type=Path,default=Path('custom_glyph_map.json')); parser.add_argument('--learned',type=Path,default=Path('learned_glyph_map.json')); parser.add_argument('--corrections',type=Path,default=Path('data/bangla_corrections.json'))
    parser.add_argument('--review',action='store_true'); parser.add_argument('--gid',type=int); parser.add_argument('--text',type=Path); parser.add_argument('--json',dest='json_path',type=Path); parser.add_argument('--generate-ttf',action='store_true',help='generate a Unicode TTF from confirmed learned glyph mappings; no PDF/page required')
    args=parser.parse_args()
    if args.generate_ttf:
        try: output,stats=generate_unicode_ttf()
        except Exception as exc: print(f'TTF GENERATION FAILED: {exc}'); return 1
        print('GENERATING UNICODE TTF'); print('======================'); print(f'Source glyph mappings : {stats["source_glyph_mappings"]}'); print(f'Single Unicode maps   : {stats["single_unicode_mappings"]}'); print(f'OpenType ligatures    : {stats["ligature_mappings"]}'); print(f'TTF: {output}'); return 0
    if args.pdf is None: parser.error('the following arguments are required: pdf (unless --generate-ttf is used)')
    if args.page is None: parser.error('--page is required unless --generate-ttf is used')
    pdf=args.pdf.read_bytes(); _cache_embedded_ttf(pdf,args.page); mapping_path=_materialize_mapping(pdf,args.page,args.mapping,args.learned); text=''; report={'missing_cids':[]}
    try:
        report=analyze_page_direct(args.pdf,args.page,mapping_path)
        if args.review and report['missing_cids']:
            review_run(args.pdf,args.page,args.learned,Path('data/bangla_conjuncts.json'),args.gid,list(report['missing_cids']),report.get('operations',[]))
            try: mapping_path.unlink(missing_ok=True)
            except PermissionError: pass
            mapping_path=_materialize_mapping(pdf,args.page,args.mapping,args.learned); report=analyze_page_direct(args.pdf,args.page,mapping_path)
        font_bytes=next((raw for _resource,_base,raw in embedded_fonts(pdf,args.page)),None); text=_logical_text(report,mapping_path,font_bytes,args.corrections)
    finally:
        try: mapping_path.unlink(missing_ok=True)
        except PermissionError: pass
    print(f'PDF: {args.pdf.name}'); print(f'PAGE: {args.page}'); print(f'MAPPED ENTRIES: {report["tounicode_entries"]}'); print(f'UNRESOLVED CIDs: {report["missing_cids"]}'); print('\n# DECODED TEXT'); print('='*72); print(text)
    if args.text: args.text.write_text(text+'\n',encoding='utf-8')
    if args.json_path: args.json_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if not report['missing_cids'] else 2

if __name__=='__main__': raise SystemExit(main())

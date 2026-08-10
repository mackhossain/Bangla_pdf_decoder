"""Generate a Unicode TTF from the project's learned glyph mappings."""
from __future__ import annotations
import json
from pathlib import Path
from fontTools.feaLib.builder import addOpenTypeFeatures
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

OUTPUT_NAME="EC_Bangla_Unicode.ttf"

def _project_root()->Path:
    return Path(__file__).resolve().parents[2]

def _discover_source_ttf(root:Path)->Path:
    # Preferred source: automatically cached embedded FontFile2 extracted
    # from an EC PDF by decoder.py. This makes --generate-ttf independent of
    # any particular PDF/page after the first normal decode/review run.
    preferred=sorted((root/"data"/"embedded_fonts").glob("*.ttf")) if (root/"data"/"embedded_fonts").exists() else []
    preferred=[p for p in preferred if p.name!=OUTPUT_NAME]
    if len(preferred)==1: return preferred[0]
    if len(preferred)>1:
        bangla=[p for p in preferred if "bangla" in p.name.lower()]
        if len(bangla)==1: return bangla[0]
        raise RuntimeError("Multiple cached embedded TTF files found; keep the correct Bangla source in data/embedded_fonts/.")

    candidates=[]
    for directory in (root,root/"data",root/"fonts",root/"font",root/"output"):
        if directory.exists(): candidates.extend(p for p in directory.rglob("*.ttf") if p.is_file())
    candidates=[p for p in candidates if p.name!=OUTPUT_NAME and "embedded_fonts" not in p.parts]
    bangla=[p for p in candidates if "bangla" in p.name.lower()]
    if len(bangla)==1: return bangla[0]
    if len(candidates)==1: return candidates[0]
    if not candidates:
        raise FileNotFoundError("No cached embedded Bangla TTF found. First run the normal decoder once on an EC PDF so its embedded FontFile2 is cached in data/embedded_fonts/.")
    names="\n".join(f"  - {p}" for p in sorted(candidates))
    raise RuntimeError("Multiple TTF files were found and the source font is ambiguous:\n"+names)

def _load_json(path:Path)->object:
    if not path.exists(): return {}
    return json.loads(path.read_text(encoding="utf-8"))

def _learned_entries(root:Path)->dict[int,str]:
    data=_load_json(root/"learned_glyph_map.json"); result={}
    fonts=data.get("fonts",{}) if isinstance(data,dict) else {}
    if isinstance(fonts,dict):
        for section in fonts.values():
            if not isinstance(section,dict): continue
            glyphs=section.get("glyphs",{})
            if not isinstance(glyphs,dict): continue
            for key,entry in glyphs.items():
                if isinstance(entry,dict) and entry.get("confidence",0)>=1.0 and isinstance(entry.get("text"),str) and entry["text"]:
                    result[int(key)]=entry["text"]
    return result

def _legacy_entries(root:Path)->dict[int,str]:
    data=_load_json(root/"custom_glyph_map.json"); result={}
    if not isinstance(data,dict): return result
    for section in data.values():
        if not isinstance(section,dict): continue
        for key,value in section.items():
            if isinstance(value,str): result[int(key)]=value
            elif isinstance(value,dict) and isinstance(value.get("text"),str): result[int(key)]=value["text"]
    return result

def _confirmed_entries(root:Path)->dict[int,str]:
    result=_legacy_entries(root); result.update(_learned_entries(root)); return result

def _build_cmap(font:TTFont,single:dict[int,str])->None:
    glyph_order=font.getGlyphOrder(); glyphs_by_unicode={}
    for gid,text in single.items():
        if len(text)==1 and 0<=gid<len(glyph_order): glyphs_by_unicode[ord(text)]=glyph_order[gid]
    cmap=newTable("cmap"); cmap.tableVersion=0; tables=[]
    bmp=CmapSubtable.newSubtable(4); bmp.platformID=3; bmp.platEncID=1; bmp.language=0; bmp.cmap={cp:gn for cp,gn in glyphs_by_unicode.items() if cp<=0xFFFF}; tables.append(bmp)
    full=CmapSubtable.newSubtable(12); full.platformID=3; full.platEncID=10; full.language=0; full.cmap=dict(glyphs_by_unicode); tables.append(full)
    uni=CmapSubtable.newSubtable(4); uni.platformID=0; uni.platEncID=3; uni.language=0; uni.cmap={cp:gn for cp,gn in glyphs_by_unicode.items() if cp<=0xFFFF}; tables.append(uni)
    cmap.tables=tables; font["cmap"]=cmap

def _build_ligatures(font:TTFont,entries:dict[int,str],single:dict[int,str])->int:
    glyph_order=font.getGlyphOrder(); reverse_single={}
    for gid,text in single.items():
        if len(text)==1 and 0<=gid<len(glyph_order): reverse_single[ord(text)]=glyph_order[gid]
    rules=[]
    for gid,text in sorted(entries.items()):
        if len(text)<=1 or gid<0 or gid>=len(glyph_order): continue
        components=[reverse_single.get(ord(ch)) for ch in text]
        if all(components): rules.append(f"sub {' '.join(components)} by {glyph_order[gid]};")
    if not rules: return 0
    addOpenTypeFeatures(font,"feature liga {\n"+"\n".join(rules)+"\n} liga;\n"); return len(rules)

def generate(output:Path|None=None)->tuple[Path,dict[str,int]]:
    root=_project_root(); source=_discover_source_ttf(root); entries=_confirmed_entries(root)
    if not entries: raise RuntimeError("No confirmed glyph mappings were found in learned_glyph_map.json/custom_glyph_map.json.")
    font=TTFont(str(source),recalcBBoxes=True,recalcTimestamp=False); single={gid:text for gid,text in entries.items() if len(text)==1}; _build_cmap(font,single); ligatures=_build_ligatures(font,entries,single)
    out=output or root/OUTPUT_NAME; font.save(str(out)); font.close()
    return out,{"source_glyph_mappings":len(entries),"single_unicode_mappings":len(single),"ligature_mappings":ligatures}

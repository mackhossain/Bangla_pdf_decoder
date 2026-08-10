"""Persistent learned mappings for custom PDF glyphs."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def load_database(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "fonts": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"mapping database must be a JSON object: {path}")
    data.setdefault("schema_version", SCHEMA_VERSION); data.setdefault("fonts", {})
    return data

def save_database(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def font_key(font_bytes: bytes, base_font: str = "") -> str:
    return f"{base_font}:{sha256_bytes(font_bytes)}"

def glyph_key(font_path: Path, gid: int) -> str:
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return f"gid:{gid}"
    font = TTFont(str(font_path), lazy=False)
    try:
        order = font.getGlyphOrder()
        if gid < 0 or gid >= len(order): return f"gid:{gid}"
        name = order[gid]; glyph = font["glyf"][name]
        parts = [name, str(getattr(glyph,"xMin",0)), str(getattr(glyph,"yMin",0)), str(getattr(glyph,"xMax",0)), str(getattr(glyph,"yMax",0))]
        if glyph.isComposite():
            parts.extend(f"{c.glyphName}:{c.xTranslate}:{c.yTranslate}:{c.transform}" for c in glyph.components)
        else:
            coords, end_pts, flags = glyph.getCoordinates(font["glyf"])
            parts.extend((repr(list(coords)), repr(list(end_pts)), repr(list(flags))))
        return "glyph:" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    finally: font.close()

def get_mapping(data: dict[str, Any], fkey: str, gid: int) -> dict[str, Any] | None:
    entry = data.get("fonts", {}).get(fkey, {}).get("glyphs", {}).get(str(gid))
    return entry if isinstance(entry, dict) else None

def build_fingerprint_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build a fast O(1) index of unambiguous confirmed glyph fingerprints."""
    index: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()
    for font in data.get("fonts", {}).values():
        if not isinstance(font, dict): continue
        for entry in font.get("glyphs", {}).values():
            if not isinstance(entry, dict) or entry.get("confidence", 0) < 1.0: continue
            gkey, text = entry.get("glyph_fingerprint"), entry.get("text")
            if not isinstance(gkey, str) or not isinstance(text, str) or not gkey: continue
            previous = index.get(gkey)
            if previous is not None and previous.get("text") != text:
                ambiguous.add(gkey)
            elif gkey not in ambiguous:
                index[gkey] = entry
    for gkey in ambiguous: index.pop(gkey, None)
    return index

def find_by_glyph_fingerprint(data: dict[str, Any], gkey: str) -> dict[str, Any] | None:
    return build_fingerprint_index(data).get(gkey)

def remember_mapping(data: dict[str, Any], *, fkey: str, base_font: str, gid: int, gkey: str, text: str, source: str = "user_confirmed", confidence: float = 1.0) -> None:
    fonts = data.setdefault("fonts", {})
    font = fonts.setdefault(fkey, {"base_font": base_font, "glyphs": {}})
    font.setdefault("base_font", base_font)
    glyphs = font.setdefault("glyphs", {})
    glyphs[str(gid)] = {"gid": gid, "text": text, "unicode": [f"U+{ord(ch):04X}" for ch in text], "glyph_fingerprint": gkey, "confidence": float(confidence), "source": source}

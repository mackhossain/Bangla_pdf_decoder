"""Build a valid Unicode TrueType font from confirmed EC glyph mappings."""
from __future__ import annotations

import json
from pathlib import Path
from fontTools.feaLib.builder import addOpenTypeFeatures
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

OUTPUT_NAME = "EC_Bangla_Unicode.ttf"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _discover_source_ttf(root: Path) -> Path:
    preferred = sorted((root / "data" / "embedded_fonts").glob("*.ttf")) if (root / "data" / "embedded_fonts").exists() else []
    preferred = [p for p in preferred if p.name != OUTPUT_NAME]
    if len(preferred) == 1:
        return preferred[0]
    if len(preferred) > 1:
        bangla = [p for p in preferred if "bangla" in p.name.lower()]
        if len(bangla) == 1:
            return bangla[0]
        raise RuntimeError("Multiple cached embedded TTF files found; keep the correct Bangla source in data/embedded_fonts/.")

    candidates = []
    for directory in (root, root / "data", root / "fonts", root / "font", root / "output"):
        if directory.exists():
            candidates.extend(p for p in directory.rglob("*.ttf") if p.is_file())
    candidates = [p for p in candidates if p.name != OUTPUT_NAME and "embedded_fonts" not in p.parts]
    bangla = [p for p in candidates if "bangla" in p.name.lower()]
    if len(bangla) == 1:
        return bangla[0]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError("No cached embedded Bangla TTF found. First run the normal decoder once on an EC PDF so its embedded FontFile2 is cached in data/embedded_fonts/.")
    names = "\n".join(f"  - {p}" for p in sorted(candidates))
    raise RuntimeError("Multiple TTF files were found and the source font is ambiguous:\n" + names)


def _load_json(path: Path) -> object:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _learned_entries(root: Path) -> dict[int, str]:
    data = _load_json(root / "learned_glyph_map.json")
    result: dict[int, str] = {}
    fonts = data.get("fonts", {}) if isinstance(data, dict) else {}
    if isinstance(fonts, dict):
        for section in fonts.values():
            if not isinstance(section, dict):
                continue
            glyphs = section.get("glyphs", {})
            if not isinstance(glyphs, dict):
                continue
            for key, entry in glyphs.items():
                if isinstance(entry, dict) and entry.get("confidence", 0) >= 1.0 and isinstance(entry.get("text"), str) and entry["text"]:
                    result[int(key)] = entry["text"]
    return result


def _legacy_entries(root: Path) -> dict[int, str]:
    data = _load_json(root / "custom_glyph_map.json")
    result: dict[int, str] = {}
    if not isinstance(data, dict):
        return result
    for section in data.values():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if isinstance(value, str):
                result[int(key)] = value
            elif isinstance(value, dict) and isinstance(value.get("text"), str):
                result[int(key)] = value["text"]
    return result


def _confirmed_entries(root: Path) -> dict[int, str]:
    result = _legacy_entries(root)
    result.update(_learned_entries(root))
    return result


def _set_names(font: TTFont) -> None:
    if "name" not in font:
        font["name"] = newTable("name")
    name = font["name"]
    values = {1: "EC Bangla Unicode", 2: "Regular", 4: "EC Bangla Unicode", 6: "ECBanglaUnicode-Regular"}
    for name_id, text in values.items():
        name.setName(text, name_id, 3, 1, 0x0409)
        name.setName(text, name_id, 1, 0, 0)


def _build_cmap(font: TTFont, single: dict[int, str]) -> None:
    glyph_order = font.getGlyphOrder()
    cmap_data: dict[int, str] = {}
    for gid, text in single.items():
        if len(text) == 1 and 0 <= gid < len(glyph_order):
            cmap_data[ord(text)] = glyph_order[gid]
    if not cmap_data:
        raise RuntimeError("No single-character Unicode mappings are available to build the cmap.")

    cmap = newTable("cmap")
    cmap.tableVersion = 0
    tables = []
    bmp = CmapSubtable.newSubtable(4)
    bmp.platformID, bmp.platEncID, bmp.language = 3, 1, 0
    bmp.cmap = {cp: gn for cp, gn in cmap_data.items() if cp <= 0xFFFF}
    tables.append(bmp)
    full = CmapSubtable.newSubtable(12)
    full.platformID, full.platEncID, full.language = 3, 10, 0
    full.cmap = dict(cmap_data)
    tables.append(full)
    uni = CmapSubtable.newSubtable(4)
    uni.platformID, uni.platEncID, uni.language = 0, 3, 0
    uni.cmap = {cp: gn for cp, gn in cmap_data.items() if cp <= 0xFFFF}
    tables.append(uni)
    cmap.tables = tables
    cmap.numTables = len(tables)
    font["cmap"] = cmap


def _build_ligatures(font: TTFont, entries: dict[int, str], single: dict[int, str]) -> int:
    glyph_order = font.getGlyphOrder()
    reverse_single: dict[int, str] = {}
    for gid, text in single.items():
        if len(text) == 1 and 0 <= gid < len(glyph_order):
            reverse_single[ord(text)] = glyph_order[gid]
    rules = []
    for gid, text in sorted(entries.items()):
        if len(text) <= 1 or gid < 0 or gid >= len(glyph_order):
            continue
        components = [reverse_single.get(ord(ch)) for ch in text]
        if all(components):
            rules.append(f"sub {' '.join(components)} by {glyph_order[gid]};")
    if not rules:
        return 0
    addOpenTypeFeatures(font, "feature liga {\n" + "\n".join(rules) + "\n} liga;\n")
    return len(rules)


def _validate_font(path: Path) -> None:
    with TTFont(str(path), checkChecksums=2, lazy=False) as check:
        if check.sfntVersion != "\x00\x01\x00\x00":
            raise RuntimeError(f"Generated file is not a TrueType sfnt (sfntVersion={check.sfntVersion!r}).")
        required = {"head", "hhea", "maxp", "name", "cmap", "glyf", "loca"}
        missing = sorted(required - set(check.keys()))
        if missing:
            raise RuntimeError("Generated TTF is missing required tables: " + ", ".join(missing))
        if not check.getBestCmap():
            raise RuntimeError("Generated TTF has no usable Unicode cmap.")


def generate(output: Path | None = None) -> tuple[Path, dict[str, int]]:
    root = _project_root()
    source = _discover_source_ttf(root)
    entries = _confirmed_entries(root)
    if not entries:
        raise RuntimeError("No confirmed glyph mappings were found in learned_glyph_map.json/custom_glyph_map.json.")

    with TTFont(str(source), lazy=False, recalcBBoxes=True, recalcTimestamp=False, checkChecksums=0) as font:
        if "glyf" not in font or "loca" not in font:
            raise RuntimeError("The cached source font is not a TrueType glyf font; a FontFile2 TrueType source is required.")
        single = {gid: text for gid, text in entries.items() if len(text) == 1}
        _set_names(font)
        _build_cmap(font, single)
        ligatures = _build_ligatures(font, entries, single)
        out = output or root / OUTPUT_NAME
        tmp = out.with_suffix(".tmp.ttf")
        try:
            font.save(str(tmp), reorderTables=True)
            _validate_font(tmp)
            tmp.replace(out)
        finally:
            if tmp.exists():
                tmp.unlink()

    return out, {"source_glyph_mappings": len(entries), "single_unicode_mappings": len(single), "ligature_mappings": ligatures}

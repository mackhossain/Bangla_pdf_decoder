"""Generate a Unicode TTF from the project's learned glyph mappings.

The generator deliberately does not inspect a PDF.  It discovers the single
source TTF already present in the project/data tree, keeps its glyph outlines,
and replaces/extends the cmap using only confirmed learned mappings.  Multi-
codepoint Bengali mappings are emitted as OpenType GSUB ligatures when all
component glyphs can be identified from the learned map.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from fontTools.feaLib.builder import addOpenTypeFeatures
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable


OUTPUT_NAME = "EC_Bangla_Unicode.ttf"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _discover_source_ttf(root: Path) -> Path:
    candidates = []
    for directory in (root, root / "data", root / "fonts", root / "font", root / "output"):
        if not directory.exists():
            continue
        candidates.extend(p for p in directory.rglob("*.ttf") if p.is_file())

    # Never treat a previously generated output as the source.
    candidates = [p for p in candidates if p.name != OUTPUT_NAME]
    if not candidates:
        raise FileNotFoundError(
            "No source TTF found. Put the extracted embedded Bangla TTF in "
            "the project root, data/, fonts/, or font/ and run --generate-ttf again."
        )

    bangla = [p for p in candidates if "bangla" in p.name.lower()]
    if len(bangla) == 1:
        return bangla[0]
    if len(candidates) == 1:
        return candidates[0]

    names = "\n".join(f"  - {p}" for p in sorted(candidates))
    raise RuntimeError(
        "Multiple TTF files were found and the source font is ambiguous:\n" + names
        + "\nKeep only the extracted Bangla source TTF in one of the supported folders."
    )


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
                if not isinstance(entry, dict) or entry.get("confidence", 0) < 1.0:
                    continue
                text = entry.get("text")
                if isinstance(text, str) and text:
                    result[int(key)] = text
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
    # Learned data wins over the legacy map because it carries explicit
    # confidence/provenance information.
    result = _legacy_entries(root)
    result.update(_learned_entries(root))
    return result


def _unicode_sequence(text: str) -> tuple[int, ...]:
    return tuple(ord(ch) for ch in text)


def _build_cmap(font: TTFont, single: dict[int, str]) -> None:
    glyph_order = font.getGlyphOrder()
    glyphs_by_unicode: dict[int, str] = {}
    for gid, text in single.items():
        if len(text) != 1 or gid < 0 or gid >= len(glyph_order):
            continue
        glyphs_by_unicode[ord(text)] = glyph_order[gid]

    cmap = newTable("cmap")
    cmap.tableVersion = 0
    tables = []

    bmp = CmapSubtable.newSubtable(4)
    bmp.platformID = 3
    bmp.platEncID = 1
    bmp.language = 0
    bmp.cmap = {cp: gn for cp, gn in glyphs_by_unicode.items() if cp <= 0xFFFF}
    tables.append(bmp)

    full = CmapSubtable.newSubtable(12)
    full.platformID = 3
    full.platEncID = 10
    full.language = 0
    full.cmap = dict(glyphs_by_unicode)
    tables.append(full)

    # Unicode platform table is useful to applications that prefer it.
    uni = CmapSubtable.newSubtable(4)
    uni.platformID = 0
    uni.platEncID = 3
    uni.language = 0
    uni.cmap = {cp: gn for cp, gn in glyphs_by_unicode.items() if cp <= 0xFFFF}
    tables.append(uni)

    cmap.tables = tables
    font["cmap"] = cmap


def _build_ligatures(font: TTFont, entries: dict[int, str], single: dict[int, str]) -> int:
    glyph_order = font.getGlyphOrder()
    reverse_single: dict[int, str] = {}
    for gid, text in single.items():
        if len(text) == 1 and 0 <= gid < len(glyph_order):
            reverse_single[ord(text)] = glyph_order[gid]

    rules: list[str] = []
    target_count = 0
    for gid, text in sorted(entries.items()):
        if len(text) <= 1 or gid < 0 or gid >= len(glyph_order):
            continue
        components = [reverse_single.get(cp) for cp in _unicode_sequence(text)]
        if not all(components):
            continue
        target = glyph_order[gid]
        rules.append(f"sub {' '.join(components)} by {target};")
        target_count += 1

    if not rules:
        return 0

    feature_text = "feature liga {\n" + "\n".join(rules) + "\n} liga;\n"
    addOpenTypeFeatures(font, feature_text)
    return target_count


def generate(output: Path | None = None) -> tuple[Path, dict[str, int]]:
    root = _project_root()
    source = _discover_source_ttf(root)
    entries = _confirmed_entries(root)
    if not entries:
        raise RuntimeError("No confirmed glyph mappings were found in learned_glyph_map.json/custom_glyph_map.json.")

    font = TTFont(str(source), recalcBBoxes=True, recalcTimestamp=False)
    single = {gid: text for gid, text in entries.items() if len(text) == 1}
    _build_cmap(font, single)
    ligatures = _build_ligatures(font, entries, single)

    out = output or (root / OUTPUT_NAME)
    font.save(str(out))
    font.close()

    stats = {
        "source_glyph_mappings": len(entries),
        "single_unicode_mappings": len(single),
        "ligature_mappings": ligatures,
    }
    return out, stats

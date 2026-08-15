"""Browser-side exact visual suggestion support for manual CID review.

v2-only: the v1 reviewer remains untouched. Candidate data and rendered
candidate SVGs are cached in RAM per embedded-font identity so repeated
unknown-CID reviews do not rebuild the same candidate set.
"""
from __future__ import annotations

import hashlib
import io
import json
from functools import lru_cache
from html import escape
from pathlib import Path

from .bangla_harfbuzz import shape_gids


def _file_signature(path: Path) -> tuple[str, int, int]:
    resolved = str(path.resolve())
    try:
        st = path.stat()
        return resolved, st.st_mtime_ns, st.st_size
    except OSError:
        return resolved, -1, -1


@lru_cache(maxsize=4)
def _cached_candidates(path_str: str, mtime_ns: int, size: int) -> tuple[tuple[str, str], ...]:
    path = Path(path_str)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in data.get("conjuncts", []):
        if not isinstance(item, dict):
            continue
        glyph = item.get("glyph")
        combination = item.get("combination")
        if not isinstance(glyph, str) or not glyph or glyph in seen:
            continue
        if not isinstance(combination, str) or not combination:
            continue
        seen.add(glyph)
        result.append((glyph, combination))
    return tuple(result)


def _candidates(path: Path) -> list[dict[str, str]]:
    path_str, mtime_ns, size = _file_signature(path)
    return [
        {"glyph": glyph, "combination": combination}
        for glyph, combination in _cached_candidates(path_str, mtime_ns, size)
    ]


def _svg_for_gids(font, gids: list[int], size: int = 128) -> str | None:
    """Render one shaped glyph sequence into a normalized fixed-size SVG canvas."""
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen

    order = font.getGlyphOrder()
    glyph_set = font.getGlyphSet()
    hmtx = font["hmtx"]
    upm = float(font["head"].unitsPerEm)
    cursor = 0.0
    bounds = None
    for gid in gids:
        if gid < 0 or gid >= len(order):
            return None
        name = order[gid]
        pen = BoundsPen(glyph_set)
        glyph_set[name].draw(pen)
        if pen.bounds:
            x0, y0, x1, y1 = pen.bounds
            b = (x0 + cursor, y0, x1 + cursor, y1)
            bounds = b if bounds is None else (
                min(bounds[0], b[0]), min(bounds[1], b[1]),
                max(bounds[2], b[2]), max(bounds[3], b[3])
            )
        cursor += float(hmtx[name][0])
    if bounds is None:
        return None

    # Normalize by actual ink bounds so an embedded ligature glyph and the
    # equivalent shaped Unicode sequence can compare on the same canvas.
    min_x, min_y, max_x, max_y = bounds
    pad = max(upm * 0.04, 4.0)
    min_x -= pad
    min_y -= pad
    max_x += pad
    max_y += pad
    width = max(max_x - min_x, 1.0)
    height = max(max_y - min_y, 1.0)
    scale = min((size - 2) / width, (size - 2) / height)
    tx = (size - width * scale) / 2 - min_x * scale
    ty = (size + height * scale) / 2 + min_y * scale

    parts = []
    cursor = 0.0
    for gid in gids:
        name = order[gid]
        pen = SVGPathPen(glyph_set)
        glyph_set[name].draw(pen)
        d = pen.getCommands()
        parts.append(
            f'<path d="{escape(d, quote=True)}" '
            f'transform="translate({tx + cursor * scale:.4f},{ty:.4f}) '
            f'scale({scale:.6f},{-scale:.6f})" fill="#000"/>'
        )
        cursor += float(hmtx[name][0])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}"><rect width="100%" height="100%" '
        f'fill="white"/>{"".join(parts)}</svg>'
    )


def _font_identity(font_bytes: bytes) -> str:
    return hashlib.sha256(font_bytes).hexdigest()


@lru_cache(maxsize=8)
def _cached_target_svg(font_identity: str, font_bytes: bytes, gid: int) -> str:
    from fontTools.ttLib import TTFont
    font = TTFont(io.BytesIO(font_bytes), lazy=False)
    try:
        return _svg_for_gids(font, [gid]) or ""
    finally:
        font.close()


@lru_cache(maxsize=4)
def _cached_candidate_svgs(
    font_identity: str,
    font_bytes: bytes,
    candidate_path_str: str,
    candidate_mtime_ns: int,
    candidate_size: int,
) -> tuple[tuple[str, str, str], ...]:
    from fontTools.ttLib import TTFont

    candidate_path = Path(candidate_path_str)
    font = TTFont(io.BytesIO(font_bytes), lazy=False)
    try:
        result = []
        for item in _candidates(candidate_path):
            shaped = shape_gids(font_bytes, item["glyph"])
            if not shaped:
                continue
            svg = _svg_for_gids(font, shaped)
            if svg:
                result.append((item["glyph"], item["combination"], svg))
        return tuple(result)
    finally:
        font.close()


def add_visual_suggestions(
    html_path: Path,
    font_path: Path,
    font_bytes: bytes,
    gid: int,
    candidate_path: Path,
) -> None:
    """Append exact visual suggestions to the existing v1 review HTML."""
    font_identity = _font_identity(font_bytes)
    target = _cached_target_svg(font_identity, font_bytes, int(gid))
    candidate_str, candidate_mtime, candidate_size = _file_signature(candidate_path)
    cached = _cached_candidate_svgs(
        font_identity,
        font_bytes,
        candidate_str,
        candidate_mtime,
        candidate_size,
    )
    candidates = [
        {"glyph": glyph, "combination": combination, "svg": svg}
        for glyph, combination, svg in cached
    ]

    payload = json.dumps(candidates, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    target_json = json.dumps(target, ensure_ascii=False)
    script = f'''<section class="visual-suggestions"><h2>Visual suggestions</h2><p class="note">Candidates use the same embedded PDF font. The browser rasterizes the target and each candidate to a canvas and compares every pixel. Only exact 100% matches are offered.</p><div id="visual-status" class="suggestion-status">Checking visual matches…</div><div id="visual-buttons" class="suggestion-buttons"></div></section><script>
window.__TARGET_SVG__={target_json};window.__VISUAL_CANDIDATES__={payload};(async()=>{{const s=document.getElementById('visual-status'),b=document.getElementById('visual-buttons'),N=128;const render=svg=>new Promise((ok,bad)=>{{const i=new Image(),u=URL.createObjectURL(new Blob([svg],{{type:'image/svg+xml;charset=utf-8'}}));i.onload=()=>{{const c=document.createElement('canvas');c.width=N;c.height=N;const x=c.getContext('2d',{{willReadFrequently:true}});x.drawImage(i,0,0,N,N);URL.revokeObjectURL(u);ok(x.getImageData(0,0,N,N).data)}};i.onerror=()=>{{URL.revokeObjectURL(u);bad()}};i.src=u}});try{{const t=await render(window.__TARGET_SVG__),m=[];for(const c of window.__VISUAL_CANDIDATES__){{const p=await render(c.svg);let same=t.length===p.length;for(let i=0;same&&i<t.length;i++)same=t[i]===p[i];if(same)m.push(c)}}if(!m.length){{s.textContent='No exact visual match found — use manual input below.';return}}s.textContent='Exact visual match(es) found:';for(const c of m){{const x=document.createElement('button');x.type='button';x.className='suggestion-button';x.title=c.combination;x.textContent=c.glyph;x.onclick=()=>{{const input=document.getElementById('unicode');input.value=c.glyph;input.focus();document.querySelector('form[action="/save"]').requestSubmit()}};b.appendChild(x)}}}}catch(e){{s.textContent='Visual comparison unavailable — use manual input below.';console.error(e)}}}})();</script>'''
    html = html_path.read_text(encoding="utf-8")
    css = '<style>.visual-suggestions{margin-top:18px;padding:16px;border:1px solid #888;border-radius:8px;background:#fafafa}.suggestion-buttons{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.suggestion-button{font-size:28px;padding:8px 14px;border:2px solid #777;border-radius:7px;background:white;cursor:pointer}.suggestion-button:hover{border-color:#111;background:#eee}.suggestion-status{font-weight:600}</style>'
    html = html.replace('</head>', css + '</head>', 1).replace('</body>', script + '</body>', 1)
    html_path.write_text(html, encoding="utf-8")

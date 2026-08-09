"""Interactive glyph review with optional AI assistance.

AI is advisory only: the user must explicitly confirm a candidate before it is
stored in learned_glyph_map.json. The same real embedded-font glyph visual used
by --review is generated and opened during --ai-review.
"""
from __future__ import annotations
import base64, html, json, os, tempfile, webbrowser
from pathlib import Path
from .bangla_candidates import generate_candidates
from .direct_pdf_fixed import embedded_fonts, ttf_gid_map, used_gids
from .learned_mapping import font_key, glyph_key, load_database, remember_mapping, save_database
from .direct_review import _svg, rank


def _glyph_png(font_path: Path, gid: int) -> bytes:
    import cairosvg
    path = _svg(font_path, gid)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="700" viewBox="-250 -250 2200 1800"><rect width="100%" height="100%" fill="white"/><path d="{path}" fill="black" transform="translate(0,1400) scale(1,-1)"/></svg>'
    return cairosvg.svg2png(bytestring=svg.encode(), output_width=900, output_height=700)


def _write_svg(path: Path, font_path: Path, gid: int) -> None:
    target = _svg(font_path, gid)
    path.write_text(
        f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="900" viewBox="-250 -200 1700 1500">
<rect width="100%" height="100%" fill="white"/>
<path d="{html.escape(target, quote=True)}" fill="black" transform="translate(0,1200) scale(1,-1)"/>
</svg>''', encoding="utf-8")


def _write_html(path: Path, font_path: Path, gid: int, ranked, ai_rows) -> None:
    encoded = base64.b64encode(font_path.read_bytes()).decode("ascii")
    target = _svg(font_path, gid)
    ai_by_text = {row['text']: row for row in ai_rows}
    cards = []
    for i, (score, text, shaped) in enumerate(ranked, 1):
        ai = ai_by_text.get(text)
        ai_html = '' if not ai else f'<div class="ai">AI confidence={ai["confidence"]:.3f}<br>{html.escape(ai["reason"])}</div>'
        cards.append(
            f'<div class="card"><div class="candidate">{html.escape(text)}</div>'
            f'<div class="meta">{i}. font score={score:.6f} shaped={html.escape(str(shaped))}</div>{ai_html}</div>'
        )
    page = f'''<!doctype html><html><head><meta charset="utf-8">
<title>EC AI Glyph Review {gid}</title>
<style>
body{{font-family:Arial;margin:24px;background:#f3f4f6;color:#111}}
.target,.card{{background:#fff;border:1px solid #aaa;border-radius:8px;padding:14px}}
.target{{max-width:900px}}
.glyph{{width:100%;height:520px;border:1px solid #888;background:#fff}}
.cards{{display:flex;flex-wrap:wrap;gap:12px;margin-top:16px}}
.card{{min-width:210px;text-align:center}}
.candidate{{font-family:EmbeddedBangla,serif;font-size:64px;padding:18px}}
.meta,.ai{{font:12px monospace;margin-top:8px}}
.ai{{background:#eef6ff;padding:8px;border-radius:6px}}
.note{{padding:10px 0;color:#444}}
@font-face{{font-family:EmbeddedBangla;src:url(data:font/ttf;base64,{encoded}) format("truetype")}}
</style></head><body>
<h1>EC AI Glyph Review — CID/GID {gid}</h1>
<div class="target"><h2>Actual PDF glyph</h2>
<div class="note">Exact outline from the embedded Bangladesh Election Commission Bangla TrueType font. AI is advisory only.</div>
<svg class="glyph" viewBox="-250 -200 1700 1500"><path d="{html.escape(target, quote=True)}" fill="black" transform="translate(0,1200) scale(1,-1)"/></svg></div>
<h2>Candidate Unicode strings</h2><div class="cards">{"".join(cards)}</div>
</body></html>'''
    path.write_text(page, encoding='utf-8')


def _ask_ai(image: bytes, *, gid: int, base_font: str, candidates: list[str], ranked):
    key = os.getenv('OPENAI_API_KEY')
    if not key:
        raise RuntimeError('OPENAI_API_KEY is not set')
    from openai import OpenAI
    model = os.getenv('OPENAI_MODEL', 'gpt-5.4-mini')
    candidate_text = '\n'.join(f'- {c}' for c in candidates)
    deterministic = '\n'.join(
        f'- {text!r}: shape_score={score:.6f}, shaped_gids={gids}'
        for score, text, gids in ranked[:20]
    )
    prompt = f'''Identify this ONE Bengali glyph from an embedded Bangladesh Election Commission TrueType font. CID/GID={gid}, font={base_font!r}. Choose only from the supplied candidates. Prefer the actual glyph image and deterministic font/HarfBuzz evidence. Do not invent Unicode. Return ONLY JSON: {{"candidates":[{{"text":"...","confidence":0.0,"reason":"..."}}]}}. At most 5 candidates, confidence 0..1.\n\nCandidates:\n{candidate_text}\n\nDeterministic candidates:\n{deterministic}'''
    data_url = 'data:image/png;base64,' + base64.b64encode(image).decode('ascii')
    response = OpenAI(api_key=key).responses.create(
        model=model,
        input=[{'role': 'user', 'content': [
            {'type': 'input_text', 'text': prompt},
            {'type': 'input_image', 'image_url': data_url},
        ]}],
    )
    parsed = json.loads(response.output_text.strip())
    allowed = set(candidates)
    result = []
    for row in parsed.get('candidates', []) if isinstance(parsed, dict) else []:
        if not isinstance(row, dict) or row.get('text') not in allowed:
            continue
        try:
            conf = max(0.0, min(1.0, float(row.get('confidence', 0))))
        except (TypeError, ValueError):
            conf = 0.0
        result.append({'text': row['text'], 'confidence': conf, 'reason': str(row.get('reason', ''))})
    return sorted(result, key=lambda x: x['confidence'], reverse=True)[:5]


def run(pdf_path: Path, page: int, db_path: Path, candidate_path: Path,
        only_gid: int | None = None, unresolved_cids: list[int] | None = None,
        use_ai: bool = True) -> None:
    pdf = pdf_path.read_bytes()
    db = load_database(db_path)
    curated = generate_candidates(candidate_path, include_generated=False) + [
        'ঁ','ং','ঃ','া','ি','ী','ু','ূ','ৃ','ে','ৈ','ো','ৌ','্','ৗ','ড়','ঢ়','য়'
    ]
    curated = sorted(set(curated), key=lambda x: (len(x), x))
    targets = list(unresolved_cids or [])
    if only_gid is not None:
        targets = [g for g in targets if g == only_gid]

    found = False
    for _resource, base_font, raw in embedded_fonts(pdf, page):
        found = True
        cmap = set(ttf_gid_map(raw))
        if unresolved_cids is None:
            targets = [g for g in used_gids(pdf, page) if g >= 120 and g not in cmap]
        if only_gid is not None:
            targets = [g for g in targets if g == only_gid]
        fkey = font_key(raw, base_font)
        with tempfile.TemporaryDirectory(prefix='ec_pdf_ai_review_') as tmp:
            font_path = Path(tmp) / 'embedded.ttf'
            font_path.write_bytes(raw)
            for gid in targets:
                existing = db.get('fonts', {}).get(fkey, {}).get('glyphs', {}).get(str(gid), {})
                if existing.get('confidence', 0) >= 1.0:
                    print(f'SKIP GID {gid}: already learned -> {existing.get("text")}', flush=True)
                    continue

                print(f'Preparing actual PDF glyph visual for AI review CID/GID {gid}...', flush=True)
                html_path = Path.cwd() / f'ai_glyph_review_{gid}.html'
                svg_path = Path.cwd() / f'ai_actual_glyph_{gid}.svg'
                _write_svg(svg_path, font_path, gid)
                ranked = rank(font_path, gid, curated)
                _write_html(html_path, font_path, gid, ranked, [])
                print(f'  SVG:  {svg_path.resolve()}', flush=True)
                print(f'  HTML: {html_path.resolve()}', flush=True)
                try:
                    webbrowser.open(html_path.resolve().as_uri(), new=2)
                except Exception as exc:
                    print(f'Could not automatically open browser: {exc}', flush=True)

                ai_rows = []
                if use_ai:
                    try:
                        ai_rows = _ask_ai(
                            _glyph_png(font_path, gid), gid=gid, base_font=base_font,
                            candidates=sorted(set(curated + [r[1] for r in ranked[:100]])),
                            ranked=ranked,
                        )
                    except Exception as exc:
                        print(f'AI REVIEW UNAVAILABLE: {exc}', flush=True)
                _write_html(html_path, font_path, gid, ranked, ai_rows)

                options = []
                seen = set()
                for row in ai_rows:
                    if row['text'] not in seen:
                        options.append((row['text'], row['confidence'], 'AI'))
                        seen.add(row['text'])
                for score, text, _gids in ranked:
                    if text not in seen:
                        options.append((text, max(0.0, 1.0 - min(1.0, score)), 'font'))
                        seen.add(text)
                    if len(options) >= 10:
                        break

                print('\n' + '=' * 72, flush=True)
                print(f'AI GLYPH REVIEW — CID/GID {gid} — font {base_font}', flush=True)
                print('=' * 72, flush=True)
                print('AI is advisory only; YOU must confirm the mapping.', flush=True)
                print(f'Visual: {html_path.resolve()}', flush=True)
                for i, (text, conf, source) in enumerate(options[:10], 1):
                    print(f'  {i:2d}. {text}   confidence={conf:.3f}   [{source}]', flush=True)

                while True:
                    answer = input('Accept [1-N], M=manual Unicode, N=next, S=skip, Q=quit: ').strip().lower()
                    if answer == 'q':
                        save_database(db_path, db)
                        return
                    if answer in {'n', 's'}:
                        break
                    if answer == 'm':
                        text = input('Enter exact Unicode character/string: ').strip()
                        if not text:
                            print('Empty mapping not accepted.', flush=True)
                            continue
                        source = 'user_confirmed'
                    elif answer.isdigit() and 1 <= int(answer) <= len(options):
                        text = options[int(answer) - 1][0]
                        source = 'user_confirmed_ai' if options[int(answer) - 1][2] == 'AI' else 'user_confirmed'
                    else:
                        print('Enter a candidate number, M, N, S, or Q.', flush=True)
                        continue
                    remember_mapping(
                        db, fkey=fkey, base_font=base_font, gid=gid,
                        gkey=glyph_key(font_path, gid), text=text,
                        source=source, confidence=1.0,
                    )
                    save_database(db_path, db)
                    print(f'LEARNED: CID/GID {gid} -> {text} | confidence=1.0', flush=True)
                    break
    if not found:
        raise RuntimeError(f'Glyph review found no embedded font on page {page}')
    save_database(db_path, db)

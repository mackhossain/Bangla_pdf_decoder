"""AI-assisted review for unresolved embedded EC Bangla glyphs.

The AI is advisory only: it never writes a mapping without an explicit human
confirmation. This keeps the learned database deterministic and auditable.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .bangla_candidates import generate_candidates
from .direct_pdf import embedded_fonts, ttf_gid_map, used_gids
from .learned_mapping import font_key, glyph_key, load_database, remember_mapping, save_database
from .direct_review import _svg, rank


def _glyph_png(font_path: Path, gid: int) -> bytes:
    """Render one embedded-font glyph outline to PNG for the vision model."""
    import cairosvg

    path = _svg(font_path, gid)
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="900" height="700"
 viewBox="-250 -250 2200 1800"><rect width="100%" height="100%" fill="white"/>
<path d="{path}" fill="black" transform="translate(0,1400) scale(1,-1)"/></svg>'''
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=900, output_height=700)


def _ask_ai(image: bytes, *, gid: int, base_font: str, candidates: list[str],
            ranked: list[tuple[float, str, list[int]]]) -> list[dict[str, Any]]:
    """Ask an OpenAI vision model for ranked candidates; never mutate state."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    from openai import OpenAI

    model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
    candidate_text = "\n".join(f"- {c}" for c in candidates)
    deterministic = "\n".join(
        f"- {text!r}: shape_score={score:.6f}, shaped_gids={gids}"
        for score, text, gids in ranked[:20]
    )
    prompt = f"""You are reviewing ONE unresolved Bengali glyph from a Bangladesh Election Commission PDF.

The embedded font is {base_font!r}. The unresolved CID/GID is {gid}.
The image is the exact outline of that glyph from the embedded TrueType font.

Your job is to identify the most likely Unicode character/string represented by THIS SINGLE GLYPH.
Do not infer a word from generic Bengali spelling alone. Prefer the actual glyph shape and the
font/HarfBuzz evidence below. Do not claim certainty you do not have.
Only choose from the supplied candidate strings. If none fits, return an empty list.

Candidate strings:
{candidate_text}

Deterministic HarfBuzz/font-shape candidates (lower numeric score is better):
{deterministic}

Return ONLY JSON in this form:
{{"candidates":[{{"text":"...","confidence":0.0,"reason":"..."}}]}}
Return at most 5 candidates, sorted by confidence descending. Confidence must be between 0 and 1.
"""
    data_url = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": data_url},
            ],
        }],
    )
    raw = response.output_text.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI returned non-JSON output: {raw[:500]!r}") from exc
    rows = parsed.get("candidates", []) if isinstance(parsed, dict) else []
    allowed = set(candidates)
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = row.get("text")
        if text not in allowed:
            continue
        try:
            confidence = max(0.0, min(1.0, float(row.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        result.append({
            "text": text,
            "confidence": confidence,
            "reason": str(row.get("reason", "")),
        })
    result.sort(key=lambda x: x["confidence"], reverse=True)
    return result[:5]


def run(pdf_path: Path, page: int, db_path: Path, candidate_path: Path,
        only_gid: int | None = None) -> None:
    """Run AI-assisted interactive review for unresolved custom glyphs."""
    pdf = pdf_path.read_bytes()
    db = load_database(db_path)

    # Keep the AI prompt bounded. The deterministic ranker still considers the
    # complete generated candidate set, while the AI sees the curated database
    # plus the best font/HarfBuzz candidates for this particular glyph.
    curated = generate_candidates(candidate_path, include_generated=False)
    curated += ["ঁ","ং","ঃ","া","ি","ী","ু","ূ","ৃ","ে","ৈ","ো","ৌ","্","ৗ","ড়","ঢ়","য়"]
    curated = sorted(set(curated), key=lambda x: (len(x), x))
    all_candidates = generate_candidates(candidate_path)

    for _resource, base_font, raw in embedded_fonts(pdf, page):
        cmap_gids = set(ttf_gid_map(raw))
        gids = [g for g in used_gids(pdf, page) if g >= 120 and g not in cmap_gids]
        if only_gid is not None:
            gids = [g for g in gids if g == only_gid]
        fkey = font_key(raw, base_font)
        with tempfile.TemporaryDirectory(prefix="ec_pdf_ai_font_") as tmp:
            font_path = Path(tmp) / "embedded.ttf"
            font_path.write_bytes(raw)
            for gid in gids:
                existing = db.get("fonts", {}).get(fkey, {}).get("glyphs", {}).get(str(gid), {})
                if existing.get("confidence", 0) >= 1.0:
                    continue

                ranked = rank(font_path, gid, all_candidates)
                ai_candidates = sorted(
                    set(curated + [row[1] for row in ranked[:100]]),
                    key=lambda x: (len(x), x),
                )
                print(f"\n{'=' * 64}")
                print(f"AI GLYPH REVIEW — CID/GID {gid} — font {base_font}")
                print(f"{'=' * 64}")
                print("The AI is advisory. Nothing is saved until YOU confirm it.")

                try:
                    image = _glyph_png(font_path, gid)
                    ai_rows = _ask_ai(image, gid=gid, base_font=base_font,
                                      candidates=ai_candidates, ranked=ranked)
                except Exception as exc:
                    print(f"AI REVIEW UNAVAILABLE: {exc}")
                    ai_rows = []

                options: list[tuple[str, float, str]] = []
                seen: set[str] = set()
                for row in ai_rows:
                    text = row["text"]
                    if text not in seen:
                        options.append((text, row["confidence"], row["reason"]))
                        seen.add(text)
                for score, text, _gids in ranked:
                    if text not in seen:
                        options.append((text, max(0.0, 1.0 - min(1.0, score)),
                                        "deterministic font/HarfBuzz candidate"))
                        seen.add(text)
                    if len(options) >= 10:
                        break

                for i, (text, confidence, reason) in enumerate(options[:10], 1):
                    source = "AI" if any(r["text"] == text for r in ai_rows) else "font"
                    print(f"  {i:2d}. {text}   confidence={confidence:.3f}   [{source}] {reason}")

                while True:
                    answer = input("Accept [1-N], M=manual Unicode, N=next, S=skip, Q=quit: ").strip().lower()
                    if answer == "q":
                        save_database(db_path, db)
                        return
                    if answer in {"n", "s"}:
                        break
                    if answer == "m":
                        text = input("Enter exact Unicode character/string: ")
                        if not text:
                            print("Empty mapping not accepted.")
                            continue
                        remember_mapping(
                            db, fkey=fkey, base_font=base_font, gid=gid,
                            gkey=glyph_key(font_path, gid), text=text,
                            source="user_confirmed", confidence=1.0,
                        )
                        save_database(db_path, db)
                        print(f"LEARNED: CID/GID {gid} -> {text} | confidence=1.0 | source=user_confirmed")
                        break
                    if answer.isdigit() and 1 <= int(answer) <= min(10, len(options)):
                        text, ai_conf, _reason = options[int(answer) - 1]
                        remember_mapping(
                            db, fkey=fkey, base_font=base_font, gid=gid,
                            gkey=glyph_key(font_path, gid), text=text,
                            source="user_confirmed_ai" if any(r["text"] == text for r in ai_rows) else "user_confirmed",
                            confidence=1.0,
                        )
                        save_database(db_path, db)
                        print(f"LEARNED: CID/GID {gid} -> {text} | confidence=1.0 | user confirmed (AI prior={ai_conf:.3f})")
                        break
                    print("Enter a candidate number, M, N, S, or Q.")
    save_database(db_path, db)

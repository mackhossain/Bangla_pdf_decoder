"""Conservative cleanup for residual Bengali PDF glyph-order artifacts.

The direct decoder and HarfBuzz validator recover the vast majority of the
logical Unicode text.  A small class of PDFs can still leave an isolated
visual-order mark or a reph after a consonant pair because the source PDF
contains atomic/custom glyphs.  This module applies only high-confidence
structural repairs and an explicit confirmed-correction dictionary.

It is intentionally conservative: ordinary logical Bengali text such as
``সেন`` must never be changed merely because ``ে`` is a Bengali pre-base mark.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CONSONANTS = "কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহড়ঢ়য়"
VOWEL_SIGNS = "ািীুূৃৄেৈোৌ"

# Confirmed from the EC page-3 output/PDF supplied during development.
# Keep these as word-level corrections, not glyph mappings.
DEFAULT_CORRECTIONS = {
    "শঁাখারী": "শাখারী",
    "অন্নপূর্না": "অন্নপূর্ণা",
    "সমর্ত": "সমর্থ",
}


def _load_corrections(path: Path | None) -> dict[str, str]:
    corrections = dict(DEFAULT_CORRECTIONS)
    if path is None or not path.exists():
        return corrections
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return corrections
    if isinstance(data, dict):
        for wrong, right in data.items():
            if isinstance(wrong, str) and isinstance(right, str) and wrong:
                corrections[wrong] = right
    return corrections


def cleanup_bangla_text(text: str, corrections_path: Path | None = None) -> str:
    """Apply only high-confidence post-shaping Bengali repairs."""
    # 1. A duplicated standalone reph is never a valid spelling artifact of
    # this PDF stream: দুর্র্গা -> দুর্গা.
    text = re.sub(r"র্(?:র্)+", "র্", text)

    # 2. A pre-base mark appearing at the beginning of a Bengali run is a
    # visual-order artifact.  Do NOT apply this to every 'ে' in a word: normal
    # logical words such as সেন must remain unchanged.
    text = re.sub(
        r"(?<![\u0980-\u09FF])([িেৈ])([" + CONSONANTS + r"])",
        r"\2\1",
        text,
    )

    # 3. Reph can occasionally be emitted after the second consonant of a
    # conjunct/custom glyph: সুবণর্া -> সুবর্ণা.  This only fires when a reph
    # follows two consecutive Bengali consonants and is followed by a vowel
    # sign, which avoids changing ordinary কর্... sequences.
    text = re.sub(
        r"([" + CONSONANTS + r"])([" + CONSONANTS + r"])র্([" + VOWEL_SIGNS + r"])",
        r"\1র্\2\3",
        text,
    )

    for wrong, right in _load_corrections(corrections_path).items():
        text = text.replace(wrong, right)

    return text


__all__ = ["cleanup_bangla_text"]

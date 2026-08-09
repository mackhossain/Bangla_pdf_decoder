"""HarfBuzz-assisted validation for reversing visually ordered EC Bengali text.

HarfBuzz normally maps logical Unicode text -> shaped glyphs.  EC PDFs are
special: their content stream already contains the shaped/visual glyph order.
We therefore use HarfBuzz in reverse as a validator: try a logical-order
candidate, shape it with the same embedded TTF, and compare the resulting GID
sequence with the PDF's original CID/GID sequence.

This module is deliberately optional at runtime.  If uharfbuzz is unavailable
or the font cannot be shaped, callers receive ``None`` and can fall back to
the deterministic Bengali reorderer.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Sequence

try:
    import uharfbuzz as hb
except ImportError:  # pragma: no cover - exercised only in minimal installs
    hb = None


def shape_gids(font_bytes: bytes, text: str) -> list[int] | None:
    """Shape Bengali Unicode text and return HarfBuzz's visual GID sequence."""
    if hb is None or not text:
        return None
    try:
        face = hb.Face(font_bytes)
        font = hb.Font(face)
        buffer = hb.Buffer()
        buffer.add_str(text)
        buffer.direction = "ltr"
        buffer.script = "beng"
        buffer.language = "bn"
        # Keep the default OpenType Indic features.  They are the important
        # part here: initial/final reordering, reph, conjuncts and vowel forms.
        hb.shape(font, buffer)
        return [int(info.codepoint) for info in buffer.glyph_infos]
    except Exception:
        return None


def gid_sequence_score(expected: Sequence[int], shaped: Sequence[int]) -> float:
    """Return a 0..1 order-sensitive similarity score for two GID sequences."""
    if not expected and not shaped:
        return 1.0
    if not expected or not shaped:
        return 0.0
    return float(SequenceMatcher(a=list(expected), b=list(shaped), autojunk=False).ratio())


def choose_candidate(
    font_bytes: bytes | None,
    expected_gids: Sequence[int],
    original: str,
    reordered: str,
) -> tuple[str, float | None]:
    """Choose the candidate whose HarfBuzz shaping best matches the PDF GIDs.

    Returns ``(text, score)``.  If HarfBuzz cannot be used, the reordered
    candidate is returned with ``None`` so the deterministic EC visual-order
    fallback remains active.
    """
    if not font_bytes or original == reordered:
        return reordered, None

    original_gids = shape_gids(font_bytes, original)
    reordered_gids = shape_gids(font_bytes, reordered)
    if original_gids is None or reordered_gids is None:
        return reordered, None

    original_score = gid_sequence_score(expected_gids, original_gids)
    reordered_score = gid_sequence_score(expected_gids, reordered_gids)

    # Require a meaningful improvement.  This prevents harmless shaping
    # differences from flipping already-correct text.
    if reordered_score > original_score + 0.02:
        return reordered, reordered_score
    return original, original_score


__all__ = ["shape_gids", "gid_sequence_score", "choose_candidate"]

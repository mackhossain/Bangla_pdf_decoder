"""Restore logical Unicode order from EC PDF's visually ordered Bangla glyph stream.

EC PDFs can emit Bengali glyphs in presentation/visual order.  This module
converts that stream back to Unicode logical order without changing the
learned CID/GID -> glyph mapping database.
"""
from __future__ import annotations

import unicodedata
from typing import Iterable, Sequence

PREBASE_MARKS = frozenset({"ি", "ে", "ৈ"})
BENGALI_CONSONANTS = frozenset(
    "কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহড়ঢ়য়"
)
BENGALI_INDEPENDENT_VOWELS = frozenset("অআইঈউঊঋএঐওঔ")
BENGALI_MARKS = frozenset("ঁংঃািীুূৃৄেৈোৌ্ৗ়")


def _has_base(token: str) -> bool:
    return any(ch in BENGALI_CONSONANTS or ch in BENGALI_INDEPENDENT_VOWELS for ch in token)


def _is_conjunct_token(token: str) -> bool:
    """Return True for a mapped glyph representing a consonant conjunct."""
    consonant_count = sum(ch in BENGALI_CONSONANTS for ch in token)
    return _has_base(token) and ("্" in token or consonant_count > 1)


def _is_separator(token: str) -> bool:
    # Keep whitespace, Latin text, digits, punctuation, etc. out of a Bangla
    # syllable run.  This prevents a malformed mark from jumping across words.
    return not any("\u0980" <= ch <= "\u09ff" for ch in token)


def _merge_reph_tokens(tokens: Sequence[str]) -> list[str]:
    """Merge separately mapped RA + VIRAMA into a reph token.

    A learned custom glyph such as CID 206 is already the single token ``র্``;
    ordinary cmap glyphs may arrive as two adjacent tokens.
    """
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens) and tokens[i] == "র" and tokens[i + 1] == "্":
            out.append("র্")
            i += 2
        else:
            out.append(tokens[i])
            i += 1
    return out


def _has_visual_order_signal(tokens: Sequence[str]) -> bool:
    """Detect evidence that this run came from visual glyph ordering.

    We deliberately do not reorder ordinary logical-order Bengali such as
    ``কেন``.  The EC PDFs provide strong signals when visual ordering is in
    effect: a pre-base mark can lead a word, can sit immediately before a
    conjunct, or a standalone reph can occur after its base glyph.
    """
    if not tokens:
        return False

    if tokens[0] in PREBASE_MARKS:
        return True

    for i, token in enumerate(tokens):
        if token == "র্":
            j = i - 1
            while j >= 0 and not _has_base(tokens[j]):
                j -= 1
            if j >= 0:
                return True

        if token in PREBASE_MARKS and i > 0 and i + 1 < len(tokens):
            if _is_conjunct_token(tokens[i + 1]):
                return True

    return False


def _move_prebase_marks(tokens: list[str]) -> None:
    """Move visual left-side vowel marks onto their following base/cluster."""
    i = 0
    while i < len(tokens):
        if tokens[i] not in PREBASE_MARKS:
            i += 1
            continue

        j = i
        marks: list[str] = []
        while j < len(tokens) and tokens[j] in PREBASE_MARKS:
            marks.append(tokens[j])
            j += 1

        k = j
        while k < len(tokens) and not _has_base(tokens[k]):
            k += 1

        if k >= len(tokens):
            i = j
            continue

        del tokens[i:j]
        target = k - (j - i) + 1
        tokens[target:target] = marks
        i = target + len(marks)


def _move_reph(tokens: list[str]) -> None:
    """Move a visually post-positioned reph before its consonant cluster."""
    i = 0
    while i < len(tokens):
        if tokens[i] != "র্":
            i += 1
            continue

        j = i - 1
        while j >= 0 and not _has_base(tokens[j]):
            j -= 1

        if j >= 0:
            reph = tokens.pop(i)
            tokens.insert(j, reph)
            i = j + 1
        else:
            i += 1


def _compose_two_part_vowel(marks: list[str]) -> list[str]:
    """Compose Bangla O/AU two-part vowel signs while preserving other marks."""
    if "ে" in marks and "া" in marks:
        marks = list(marks)
        marks.remove("ে")
        marks.remove("া")
        marks.insert(0, "ো")
    elif "ে" in marks and "ৗ" in marks:
        marks = list(marks)
        marks.remove("ে")
        marks.remove("ৗ")
        marks.insert(0, "ৌ")
    return marks


def _reorder_run(tokens: Sequence[str]) -> list[str]:
    working = _merge_reph_tokens(tokens)

    if not _has_visual_order_signal(working):
        return working

    _move_prebase_marks(working)
    _move_reph(working)

    # After the visual moves, the two halves of a Bangla vowel can be adjacent
    # in reverse visual order (e.g. ``া`` then ``ে``).  Put them into logical
    # order and compose U+09CB/U+09CC explicitly.  We intentionally avoid a
    # global NFC pass because Bengali letters such as য় have canonical
    # decompositions and should not be rewritten unnecessarily.
    out: list[str] = []
    i = 0
    while i < len(working):
        token = working[i]
        out.append(token)
        i += 1

        if not _has_base(token):
            continue

        marks: list[str] = []
        while i < len(working) and working[i] in BENGALI_MARKS and working[i] != "্":
            marks.append(working[i])
            i += 1
        out.extend(_compose_two_part_vowel(marks))

    return out


def restore_bangla_logical_order_tokens(tokens: Iterable[str]) -> list[str]:
    """Restore logical order while preserving PDF glyph-token boundaries."""
    output: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            output.extend(_reorder_run(run))
            run.clear()

    for token in tokens:
        if _is_separator(token):
            flush()
            output.append(token)
        else:
            run.append(token)
    flush()
    return output


def restore_bangla_logical_order(text: str) -> str:
    """Convenience API for already-decoded text.

    This character-level fallback is useful for callers that do not have the
    PDF glyph-token list.  Decoder.py uses the token-preserving API instead.
    """
    tokens = list(text)
    return "".join(restore_bangla_logical_order_tokens(tokens))


__all__ = [
    "restore_bangla_logical_order",
    "restore_bangla_logical_order_tokens",
]

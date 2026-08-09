"""Restore logical Unicode order from EC PDF's visually ordered Bangla glyph stream.

EC PDFs can emit Bengali glyphs in presentation/visual order. This module
performs the deterministic half of the recovery: pre-base vowel marks and
post-positioned reph glyphs are moved back into Unicode logical order, while
whole learned conjunct/custom glyphs remain atomic.

HarfBuzz is used by the decoder as a second-stage validator. The reorderer
therefore follows the same broad Indic shaping ideas (syllable/cluster units,
pre-base marks, reph, and two-part vowels) without trying to reimplement the
entire OpenType shaper.
"""
from __future__ import annotations

from typing import Iterable, Sequence

PREBASE_MARKS = frozenset({"ি", "ে", "ৈ"})
VOWEL_SIGNS = frozenset({"া", "ি", "ী", "ু", "ূ", "ৃ", "ৄ", "ে", "ৈ", "ো", "ৌ"})
BENGALI_CONSONANTS = frozenset(
    "কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহড়ঢ়য়"
)
BENGALI_INDEPENDENT_VOWELS = frozenset("অআইঈউঊঋএঐওঔ")
BENGALI_MARKS = frozenset("ঁংঃািীুূৃৄেৈোৌ্ৗ়")


def _has_base(token: str) -> bool:
    return any(ch in BENGALI_CONSONANTS or ch in BENGALI_INDEPENDENT_VOWELS for ch in token)


def _is_separator(token: str) -> bool:
    return not any("\u0980" <= ch <= "\u09ff" for ch in token)


def _merge_reph_tokens(tokens: Sequence[str]) -> list[str]:
    """Merge separately mapped RA + VIRAMA into one atomic reph token."""
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
    """Detect the strongest evidence that a run is visually ordered."""
    if not tokens:
        return False
    if tokens[0] in PREBASE_MARKS:
        return True
    for i, token in enumerate(tokens):
        if token == "র্" and any(_has_base(t) for t in tokens[:i]):
            return True
    return False


def _move_prebase_marks(tokens: list[str], *, aggressive: bool) -> None:
    """Move visual left-side vowel marks after their following base/cluster."""
    if not aggressive and not _has_visual_order_signal(tokens):
        return

    i = 0
    while i < len(tokens):
        if tokens[i] not in PREBASE_MARKS:
            i += 1
            continue

        # In a two-part vowel, ে/ৈ followed immediately by ্? no; the common
        # Bengali visual pair is ে+া or ে+ৗ. Keep that pair attached to the
        # current base rather than moving the first half to the next base.
        if i + 1 < len(tokens) and tokens[i] in {"ে", "ৈ"} and tokens[i + 1] in {"া", "ৗ"}:
            i += 2
            continue

        start = i
        j = i
        marks: list[str] = []
        while j < len(tokens) and tokens[j] in PREBASE_MARKS:
            # A second mark that is part of a two-part vowel belongs to the
            # first mark and must not be moved independently.
            if (
                j + 1 < len(tokens)
                and tokens[j] in {"ে", "ৈ"}
                and tokens[j + 1] in {"া", "ৗ"}
            ):
                break
            marks.append(tokens[j])
            j += 1

        if not marks:
            i += 1
            continue

        k = j
        while k < len(tokens) and not _has_base(tokens[k]):
            k += 1
        if k >= len(tokens):
            i = j
            continue

        removed = j - start
        del tokens[start:j]
        insert_at = k - removed + 1
        tokens[insert_at:insert_at] = marks
        i = insert_at + len(marks)


def _move_reph(tokens: list[str]) -> None:
    """Move a visually post-positioned standalone reph before its base."""
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
    """Compose Bangla O/AU two-part vowel signs."""
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


def _collect_marks(tokens: Sequence[str], start: int) -> tuple[list[str], int]:
    """Collect one vowel sign plus trailing non-vowel marks."""
    marks: list[str] = []
    i = start
    seen_vowel = False
    while i < len(tokens):
        token = tokens[i]
        if token == "্":
            marks.append(token)
            i += 1
            continue
        if token not in BENGALI_MARKS:
            break
        if token in VOWEL_SIGNS:
            if seen_vowel:
                break
            seen_vowel = True
        marks.append(token)
        i += 1
    return marks, i


def _reorder_run(tokens: Sequence[str], *, visual_order: bool) -> list[str]:
    working = _merge_reph_tokens(tokens)
    if not working:
        return []

    _move_prebase_marks(working, aggressive=visual_order)
    if visual_order or _has_visual_order_signal(working):
        _move_reph(working)

    out: list[str] = []
    i = 0
    while i < len(working):
        token = working[i]
        out.append(token)
        i += 1

        if not _has_base(token):
            continue

        marks, i = _collect_marks(working, i)
        if not marks:
            continue

        if "্" in marks:
            virama_index = marks.index("্")
            before = marks[:virama_index]
            after = marks[virama_index + 1 :]
            out.extend(_compose_two_part_vowel(before))
            out.append("্")
            out.extend(_compose_two_part_vowel(after))
        else:
            out.extend(_compose_two_part_vowel(marks))

    return out


def restore_bangla_logical_order_tokens(
    tokens: Iterable[str], *, visual_order: bool = False
) -> list[str]:
    """Restore logical order while preserving PDF glyph-token boundaries."""
    output: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            output.extend(_reorder_run(run, visual_order=visual_order))
            run.clear()

    for token in tokens:
        if _is_separator(token):
            flush()
            output.append(token)
        else:
            run.append(token)
    flush()
    return output


def restore_bangla_logical_order(text: str, *, visual_order: bool = False) -> str:
    """Convenience API for already-decoded text."""
    return "".join(restore_bangla_logical_order_tokens(list(text), visual_order=visual_order))


__all__ = [
    "restore_bangla_logical_order",
    "restore_bangla_logical_order_tokens",
]

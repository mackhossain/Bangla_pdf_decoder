"""Bangla conjunct candidate generation.

The Unicode model for Bangla represents conjuncts as consonant + U+09CD +
consonant sequences; a visual conjunct is therefore not a separate Unicode
character.  We keep a curated high-value list and can also generate pairwise
and selected triple clusters so the interactive mapper has a broad candidate
pool without pretending that every orthographic cluster is equally likely.
"""
from __future__ import annotations

import json
from pathlib import Path

VIRAMA = "্"
CONSONANTS = "কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহ"


def load_candidates(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    values = data.get("conjuncts", [])
    if not isinstance(values, list):
        raise ValueError(f"invalid conjunct database: {path}")
    return [str(x) for x in values if isinstance(x, str) and x]


def generate_candidates(path: Path, *, include_generated: bool = True) -> list[str]:
    base = load_candidates(path)
    if not include_generated:
        return sorted(set(base))

    result = set(base)
    # Two-consonant Unicode conjunct sequences.
    for a in CONSONANTS:
        for b in CONSONANTS:
            result.add(a + VIRAMA + b)

    # Triple clusters are useful but much more combinatorial.  Generate them
    # only for the second/third members commonly used in Bangla orthography.
    common_tail = "করগঘলমযরবভশষসতদনপফ"
    for a in CONSONANTS:
        for b in common_tail:
            for c in common_tail:
                result.add(a + VIRAMA + b + VIRAMA + c)

    return sorted(result, key=lambda s: (len(s), s))

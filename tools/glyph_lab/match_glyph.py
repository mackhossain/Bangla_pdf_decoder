#!/usr/bin/env python3
"""Compare one glyph PNG against a directory of saved glyph PNGs.

The comparison is deliberately independent of fonts, HarfBuzz, SVG, and the
browser. PNG is lossless, so the saved pixels are the comparison data.

Requirements:
    pip install pillow

Example:
    python tools/glyph_lab/match_glyph.py target.png glyph_lab --top 10
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageStat


def load(path: Path) -> Image.Image:
    with Image.open(path) as im:
        # Canonical comparison representation: 8-bit grayscale pixels.
        return im.convert("L").copy()


def compare(a: Image.Image, b: Image.Image) -> tuple[bool, float, int]:
    if a.size != b.size:
        return False, 0.0, -1
    diff = ImageChops.difference(a, b)
    if diff.getbbox() is None:
        return True, 100.0, 0

    # Mean absolute pixel difference converted to a 0..100 similarity score.
    mean_diff = ImageStat.Stat(diff).mean[0]
    score = max(0.0, 100.0 * (1.0 - mean_diff / 255.0))
    changed = sum(1 for px in diff.getdata() if px != 0)
    return False, score, changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=Path)
    ap.add_argument("candidates", type=Path)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--exact-only", action="store_true")
    args = ap.parse_args()

    target = load(args.target)
    rows: list[tuple[float, bool, int, Path]] = []

    for path in sorted(args.candidates.rglob("*.png")):
        if path.resolve() == args.target.resolve():
            continue
        try:
            candidate = load(path)
        except Exception as exc:
            print(f"SKIP {path}: {exc}")
            continue
        exact, score, changed = compare(target, candidate)
        if exact:
            print(f"EXACT MATCH: {path}")
        if not args.exact_only or exact:
            rows.append((score, exact, changed, path))

    rows.sort(key=lambda x: (not x[1], -x[0], x[2], str(x[3])))

    print("\nMATCH RESULTS")
    print("=" * 72)
    print(f"TARGET: {args.target}")
    print(f"SIZE:   {target.size[0]}x{target.size[1]}")
    print("-" * 72)

    if not rows:
        print("No candidate PNGs found.")
        return 0

    for score, exact, changed, path in rows[: max(1, args.top)]:
        marker = "EXACT" if exact else "     "
        print(f"{marker}  {score:8.4f}%  changed_pixels={changed:7d}  {path}")

    print("=" * 72)
    print("100.0000% means every saved pixel is identical.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

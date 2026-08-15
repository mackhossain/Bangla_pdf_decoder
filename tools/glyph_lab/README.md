# Glyph Lab — isolated visual matching experiment

This experiment is intentionally separate from the decoder review workflow.
It lets us prove the image-matching idea before putting it into v2.

## 1. Install the small dependencies

```bash
pip install fonttools uharfbuzz cairosvg pillow
```

## 2. Generate one reference glyph

Use the **exact TTF extracted from the PDF** that we want to investigate:

```bash
python tools/glyph_lab/make_glyph.py --font path\to\embedded.ttf --text "শ্চ" --name shcho
```

This creates:

```text
glyph_lab/
    shcho.svg
    shcho.png
    shcho.json
```

The SVG is the vector reference. The PNG is lossless and is the actual pixel-comparison reference. The JSON records the Unicode text, shaped GIDs, canvas size, and SHA-256 of the font.

For another character:

```bash
python tools/glyph_lab/make_glyph.py --font path\to\embedded.ttf --text "চ" --name cha
```

## 3. Compare saved images

```bash
python tools/glyph_lab/match_glyph.py glyph_lab/shcho.png glyph_lab --top 10
```

The matcher does **not** render anything and does **not** use the font. It only loads the saved PNG pixels and compares them.

Example output:

```text
MATCH RESULTS
========================================================================
TARGET: glyph_lab/shcho.png
SIZE:   256x256
------------------------------------------------------------------------
EXACT  100.0000%  changed_pixels=      0  glyph_lab/shcho_copy.png
         83.1234%  changed_pixels=   4210  glyph_lab/other.png
========================================================================
100.0000% means every saved pixel is identical.
```

## Important test

Generate the **same text twice with the same font and settings**. Those PNGs should compare at exactly 100%.

Then generate a visually different character such as `চ`. It should not be 100%.

This isolates the question we are debugging:

**Can the exact embedded font render a candidate conjunct into pixels that are identical to the PDF glyph pixels?**

Do not modify `v1.0.0` or the existing v2 review code while running this experiment.

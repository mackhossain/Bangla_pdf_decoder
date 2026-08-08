# EC PDF Decoder

A deterministic, non-OCR decoder for Bangladesh Election Commission voter-list PDFs.

## Production decoder

```cmd
python decoder.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3
```

Write Unicode text:

```cmd
python decoder.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --text page3.txt
```

Write diagnostics:

```cmd
python decoder.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --json page3.json
```

## Interactive glyph learning

When a PDF contains custom CIDs/GIDs that are not yet mapped, run:

```cmd
python decoder.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --review
```

The learner:

1. extracts the embedded TrueType font from the PDF;
2. identifies the actual custom GID used by the page;
3. generates Bangla conjunct and sign candidates;
4. uses HarfBuzz shaping when available to find exact GSUB matches;
5. ranks the remaining candidates using glyph geometry;
6. opens an HTML visual comparison showing the real embedded PDF glyph and candidate renderings in the same font;
7. accepts a candidate by number, skips it with `S`, advances with `N`, or exits with `Q`;
8. stores a confirmed answer at confidence `1.0` in `learned_glyph_map.json`.

Review a single glyph:

```cmd
python review_mappings.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --gid 290
```

The learned database is scoped by the SHA-256 fingerprint of the embedded font, not by CID alone. This is important because CID 290 in another embedded font is not guaranteed to mean `ন্ত`.

## Existing validated mappings

The repository currently contains evidence-backed mappings for the inspected target font:

```text
CID/GID 207 -> ে
CID/GID 290 -> ন্ত
```

These remain in `custom_glyph_map.json`. New user-confirmed mappings are stored separately in `learned_glyph_map.json` and are automatically materialized into the decoder for the matching embedded font.

## Bangla conjunct candidates

`data/bangla_conjuncts.json` contains a curated high-value Bangla conjunct list. The candidate generator also creates pairwise consonant + virama + consonant sequences and selected triple clusters. Unicode models Bangla conjuncts as sequences using U+09CD VIRAMA rather than as a separate Unicode character. See the Unicode Standard's Bangla rendering description for this model.

## Accuracy policy

The decoder never silently converts an unknown custom glyph to a guessed Unicode character. An unresolved CID is reported explicitly. A mapping becomes automatic only after deterministic PDF evidence or explicit user confirmation. User confirmation is stored with confidence `1.0` and tied to the exact embedded font fingerprint.

## Low-level tools

The existing diagnostic tools remain available, including:

```cmd
python decode_pdf.py FILE.pdf --page 3
python -m src.ec_pdf_decoder.extract FILE.pdf --page 3 --mapping custom_glyph_map.json --json page3_analysis.json
```

The old `analyze_custom_context.py` workflow is still useful for inspecting raw CID context, but the new learner is the recommended path for resolving unknown custom glyphs.

## Privacy

Election/voter PDFs can contain sensitive personal information. Do not commit real voter PDFs, voter databases, credentials, or access tokens to the repository.

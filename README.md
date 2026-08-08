# EC PDF Decoder

A byte-accurate, non-OCR decoder for Bangladesh Election Commission voter-list PDFs.

## Current decoding path

```text
PDF bytes
  -> page/content streams
  -> raw Tj/TJ byte strings
  -> 2-byte Identity-H CIDs
  -> font resource
  -> /ToUnicode CMap
  -> validated custom-glyph overrides
  -> logical Bangla Unicode
```

The project never treats rendered glyph appearance as Unicode truth. It preserves PDF strings as bytes and uses the PDF's declared encoding first.

## Target PDF

The reverse-engineering target contains:

```text
/BaseFont /CQCQVJ+Bangla
/Subtype /Type0
/Encoding /Identity-H
/DescendantFonts [235 0 R]
/ToUnicode 236 0 R
```

The descendant is a CIDFontType2 with an Identity CID-to-GID mapping. Therefore, for this font, the 2-byte CID value identifies the same numeric GID in the embedded TrueType font.

## New deterministic decoder

`src/ec_pdf_decoder/extract.py` now handles:

- PDF object and page discovery
- FlateDecode streams
- compact PDF dictionaries such as `<</Filter/FlateDecode/Length 123>>`
- raw `Tj`, `TJ`, `'` and `"` recovery
- Identity-H 2-byte CID decoding
- `/ToUnicode` `bfchar` and `bfrange`
- multi-codepoint Unicode destinations
- CID usage/frequency reporting
- unresolved CID reporting
- JSON diagnostics
- validated custom-glyph overrides

Run page 3:

```cmd
python -m src.ec_pdf_decoder.extract 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --mapping custom_glyph_map.json
```

Write a machine-readable report:

```cmd
python -m src.ec_pdf_decoder.extract 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --mapping custom_glyph_map.json --json page3_analysis.json
```

Unresolved CIDs are displayed as `⟦CID:n⟧` instead of being guessed.

## Custom-glyph context analyzer

The old experimental `bytes`/regex failure is replaced by a byte-safe analyzer:

```cmd
python analyze_custom_context.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf 3
```

It reports only CIDs that remain unresolved after `/ToUnicode` and the validated mapping file, together with neighboring CID values, decoded context, and raw hex.

## Validated target-font observations

Current reverse-engineering evidence establishes:

```text
CQCQVJ+Bangla
GID 207 -> ে
GID 290 -> ন্ত
```

These are stored in `custom_glyph_map.json` as validated overrides. More mappings must be added only after evidence is available; outline-similarity results are not sufficient proof for a ligature or multi-codepoint glyph.

## Tests

```cmd
python -m pytest -q
```

Regression coverage includes Identity-H CID decoding and a multi-codepoint `/ToUnicode` mapping for `ন্ত`.

## Important accuracy rule

The decoder does not promise a percentage of accuracy independent of the PDF. Where the PDF contains enough information, it should deterministically reproduce logical Unicode text. Where information is insufficient, it reports the exact unresolved CID/GID instead of silently inventing a character.

## Privacy

Election/voter PDFs contain sensitive personal information. Do not commit real voter PDFs, databases, credentials, or access tokens to this repository.

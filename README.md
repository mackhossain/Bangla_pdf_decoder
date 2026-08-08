# EC PDF Decoder

A byte-accurate, non-OCR decoder for Bangladesh Election Commission voter-list PDFs.

## Production path

```text
PDF bytes
  -> page/content streams
  -> raw Tj/TJ byte strings
  -> 2-byte Identity-H CIDs
  -> /ToUnicode CMap
  -> validated custom-glyph overrides
  -> logical Bangla Unicode
```

The decoder keeps PDF strings as bytes until the font encoding layer knows how to interpret them. It does not OCR and does not guess Unicode from glyph-outline similarity.

## Target font

The reverse-engineered target uses:

```text
/BaseFont /CQCQVJ+Bangla
/Subtype /Type0
/Encoding /Identity-H
/DescendantFonts [235 0 R]
/ToUnicode 236 0 R
```

Its descendant is a CIDFontType2 with `/CIDToGIDMap /Identity`, so CID and GID are numerically identical for this font.

## Production command

From the repository root:

```cmd
python decode_pdf.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3
```

Write Unicode text to a file:

```cmd
python decode_pdf.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --text page3.txt
```

Write diagnostics as JSON:

```cmd
python decode_pdf.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --json page3.json
```

The default `custom_glyph_map.json` is loaded automatically. The command exits with code `0` when no unresolved CID remains and `2` when unresolved CIDs remain.

## Mapping policy

The production mapping currently contains only evidence-backed observations for the target font:

```text
GID/CID 207 -> ে
GID/CID 290 -> ন্ত
```

They are stored in `custom_glyph_map.json`. Do not add a mapping merely because a glyph-outline similarity tool produced a nearby Unicode glyph. A custom ligature needs evidence from the PDF's text context and/or a trusted visual/reference transcription.

## Finding the remaining custom glyphs

Run:

```cmd
python analyze_custom_context.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf 3 --mapping custom_glyph_map.json > custom_context_page3.txt
```

This analyzer is mapping-aware. Already validated mappings are removed from the unresolved list, so the output focuses only on the next CIDs that genuinely need investigation. It prints the surrounding CID/GID sequence, already-decoded Unicode context, and raw PDF hex.

The previous `bytes object has no attribute finditer` failure is avoided: PDF data remains bytes and the analyzer does not run text regex operations over bytes content.

## Low-level diagnostic command

For the complete diagnostic report:

```cmd
python -m src.ec_pdf_decoder.extract 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --mapping custom_glyph_map.json --json page3_analysis.json
```

Unresolved CIDs are rendered as `⟦CID:n⟧` in diagnostic decoding rather than silently guessed.

## What is implemented

- PDF object/page discovery
- FlateDecode stream handling
- `Tj`, `TJ`, `'` and `"` extraction
- Identity-H two-byte CID decoding
- `/ToUnicode` `bfchar` and `bfrange`
- multi-codepoint Unicode destinations
- validated custom-glyph overrides
- CID frequency and unresolved-CID diagnostics
- mapping-aware custom-glyph context analysis
- JSON diagnostics

## Accuracy rule

The project aims for deterministic decoding, not an invented accuracy percentage. If the PDF contains sufficient mapping information, the decoder should reproduce logical Unicode text. If information is insufficient, it reports the exact unresolved CID/GID instead of silently inventing a character.

## Privacy

Election/voter PDFs can contain sensitive personal information. Do not commit real voter PDFs, voter databases, credentials, or access tokens to the repository.

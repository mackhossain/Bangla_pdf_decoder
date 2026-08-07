# EC PDF Decoder

A byte-first, non-OCR PDF extraction toolkit for Bangladesh Election Commission voter-list PDFs.

The project is designed for PDFs that **render Bangla correctly in Acrobat/browser viewers but produce corrupted text when copied or extracted by generic tools**. Instead of OCR or a guessed character table, the decoder follows the PDF's own data path: objects → streams → content operators → font character codes → embedded `/ToUnicode` CMap → Unicode.

## Why this matters

The target EC PDFs contain real selectable text. A typical text resource looks like:

```text
/BaseFont /CQCQVJ+Bangla
/Subtype /Type0
/Encoding /Identity-H
/ToUnicode 236 0 R
/DescendantFonts [235 0 R]
```

The bytes inside `Tj` operations are **font character codes**, not ordinary UTF-8 and not necessarily direct Unicode. For example, a decoded content stream contains values such as:

```text
<002f004f0059004b0122000300cf0041004b...> Tj
```

The decoder must interpret those codes according to the font's CMap codespaces and `/ToUnicode` mappings.

## Current extraction pipeline

```text
PDF file
   │
   ├── xref / indirect objects
   │
   ├── page /Contents stream
   │       │
   │       └── FlateDecode
   │
   ├── PDF content scanner
   │       │
   │       └── Tj / TJ / ' / "
   │
   ├── raw font character-code bytes
   │       │
   │       └── /F1 → Type0 font
   │
   ├── /ToUnicode CMap
   │       │
   │       ├── codespace ranges
   │       ├── bfchar
   │       └── bfrange
   │
   └── logical Unicode text
```

The current real-PDF target has been verified to expose font object `6 0 R`, descendant font `235 0 R`, and `/ToUnicode` object `236 0 R`. Its CMap contains 77 mappings, including mappings such as `001B → U+0981`, `0038 → U+09A3`, and `0078 → U+25CC`.

## Implemented components

### PDF content scanner

`src/ec_pdf_decoder/content.py` tokenizes page content streams while preserving PDF string bytes. It supports:

- literal strings and PDF escapes
- hexadecimal strings
- names
- numbers
- arrays
- comments
- text operators `Tj`, `TJ`, `'`, and `"`
- recoverable scanning for malformed real-world streams
- raw text-show operation preservation

### Raw content-stream dumper

`src/ec_pdf_decoder/rawdump.py` is the diagnostic tool used to inspect decoded page streams byte-for-byte. It is intentionally separate from Unicode extraction so malformed or unusual content can be reverse-engineered without losing the original evidence.

Example:

```cmd
python -m ec_pdf_decoder.rawdump 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --max-bytes 1000
```

### Stream decoding

`src/ec_pdf_decoder/extract.py` currently handles the Flate stream used by the target PDF. A compact dictionary such as:

```text
<</Filter/FlateDecode/Length 2777>>
```

is supported.

### ToUnicode CMap parser

`src/ec_pdf_decoder/cmap.py` parses:

- `begincodespacerange`
- `beginbfchar`
- `beginbfrange`
- single and multi-code-unit Unicode destinations
- bfrange destination arrays

It also decodes arbitrary font-code bytes according to the CMap's codespace widths. The decoder prefers the longest matching codespace width, which is important for Type0/Identity-H fonts containing two-byte character codes.

### Text decoding layer

`src/ec_pdf_decoder/textdecode.py` converts `Tj` and `TJ` operations through a parsed `CMap`.

`TJ` numeric positioning adjustments are deliberately ignored for logical text extraction; byte-string elements are decoded in their original order.

### End-to-end Unicode extractor

`src/ec_pdf_decoder/unicode_extract.py` connects the real PDF content stream to the embedded ToUnicode CMap.

Run:

```cmd
python -m ec_pdf_decoder.unicode_extract 261694_com_1267_female_without_photo_71_2025-11-24.pdf
```

The output is page-by-page Unicode text:

```text
PDF: 261694_com_1267_female_without_photo_71_2025-11-24.pdf
Pages: 73

=== PAGE 1 ===
...

=== PAGE 2 ===
...
```

The default target font is object `6 0 R`. A different Type0 font object can be selected with:

```cmd
python -m ec_pdf_decoder.unicode_extract file.pdf --font-object 6
```

## Important accuracy rule

The project does **not** claim a generic percentage such as "99% accurate". The goal is deterministic decoding from the PDF's own data.

When a PDF supplies a valid character-code → `/ToUnicode` mapping, that mapping is the source of truth. If the PDF contains an unmapped code, malformed stream, or insufficient information, the decoder should expose that condition rather than silently inventing a character.

This is intentionally different from OCR: the system is recovering the text represented by the PDF's own font and content structures.

## Development

Python 3.10+ is recommended.

Create a virtual environment on Windows:

```cmd
python -m venv .venv
.venv\Scripts\activate
```

Install development dependencies used by the project:

```cmd
pip install fonttools rich pytest
```

Run the complete test suite:

```cmd
python -m pytest -q
```

Run a focused test:

```cmd
python -m pytest tests\test_textdecode.py -q
```

## Real-PDF reverse-engineering workflow

For a new EC PDF, the recommended order is:

1. Inspect the PDF/object structure.
2. Identify the page `/Contents` objects.
3. Decode `/FlateDecode` streams.
4. Use `rawdump` to verify that the decoded bytes contain normal PDF operators.
5. Confirm `Tj`/`TJ` operations are being recovered.
6. Identify the active font resource.
7. Resolve `/ToUnicode`.
8. Parse codespaces and mappings.
9. Decode the raw font codes to Unicode.
10. Only after that, add Bangla-specific reconstruction or voter-record parsing.

This ordering prevents OCR, visual appearance, or copy/paste behavior from becoming an accidental source of truth.

## Privacy

Election and voter-list PDFs can contain sensitive personal information. Keep real voter datasets and sample PDFs private unless publication is intentional. Do not commit credentials, access tokens, personal databases, or unrelated voter data.

## Project status

**Active reverse-engineering / extraction stage.**

The current implementation has progressed from PDF lexical/object parsing through stream decompression, recoverable content scanning, real ToUnicode CMap inspection, and end-to-end Unicode decoding for the target EC PDF. The next layers are font-resource selection beyond the current target font, validation against more EC PDF variants, Bangla-specific reconstruction where the source PDF requires it, and structured voter-record extraction.

## License

License terms have not yet been finalized.

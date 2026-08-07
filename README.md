# EC PDF Decoder

A byte-accurate, non-OCR PDF extraction toolkit being developed specifically for Bangladesh Election Commission voter-list PDFs.

## Why this project exists

Some Bangladesh Election Commission PDFs display Bangla text correctly in Adobe Acrobat and modern browsers, yet copied text can become corrupted or appear as private-use or unrelated Unicode characters. The underlying PDF can still contain real text: the page may use an embedded Bangla TrueType font, a CID font with `Identity-H` encoding, and a `/ToUnicode` CMap.

This project takes the PDF apart at the PDF-object and font-encoding level instead of treating the page as an image. The goal is to recover the original logical Bangla Unicode text without OCR whenever the PDF itself contains enough information to do so.

## Current target PDF characteristics

The initial reverse-engineering target contains structures such as:

```text
/BaseFont /CQCQVJ+Bangla
/Subtype /Type0
/Encoding /Identity-H
/ToUnicode 236 0 R
/DescendantFonts [235 0 R]
```

The corresponding `/ToUnicode` stream maps CID values to Unicode code points. This is important because the glyphs shown on the page are not necessarily the Unicode characters produced by ordinary copy/paste.

## Design principles

- **No OCR for the core extraction path.** The decoder works from the PDF's own objects, content streams, CMaps, and embedded fonts.
- **Byte-first processing.** PDF strings and streams are kept as bytes until the layer that actually knows how to interpret them.
- **Layered architecture.** Lexing, object parsing, xref/document access, stream decoding, CMap handling, font handling, content interpretation, Bangla reconstruction, and voter-record extraction remain separate concerns.
- **Tests before expansion.** Each layer should have focused tests before higher-level decoding depends on it.
- **Diagnostics matter.** Offsets, object numbers, generations, token types, and decoding decisions should remain inspectable so difficult PDFs can be reverse-engineered rather than guessed at.
- **Accuracy over convenience.** Generic PDF text-extraction libraries may be useful for comparison, but they are not the source of truth for this decoder.

## Architecture

The intended package is organized approximately as follows:

```text
ec_pdf_decoder/
├── src/
│   └── ec_pdf_decoder/
│       ├── __init__.py
│       ├── lexer.py       # PDF lexical scanner
│       ├── objects.py     # PDF value/object model
│       ├── parser.py      # Recursive PDF object parser
│       ├── xref.py        # Cross-reference and trailer handling
│       ├── streams.py     # PDF stream/filter decoding
│       ├── cmap.py        # ToUnicode CMap parsing
│       ├── font.py        # Embedded font inspection/mapping
│       ├── content.py     # Page content operators
│       ├── bangla.py      # Bangla-specific reconstruction logic
│       └── decoder.py     # High-level extraction API
│
├── tests/
├── README.md
└── samples/               # Local test PDFs; keep sensitive documents private
```

Not every module in the architecture is implemented yet. The repository is being built incrementally so each stage can be verified against real Election Commission PDFs.

## Implemented so far

### Lexer

`src/ec_pdf_decoder/lexer.py` provides a byte-oriented PDF lexer with support for:

- whitespace and comments
- integer and real numbers
- PDF names
- `#XX` hexadecimal name escapes
- literal strings
- nested literal strings
- PDF string escape sequences
- octal string escapes
- hexadecimal strings
- arrays (`[` and `]`)
- dictionaries (`<<` and `>>`)
- `true`, `false`, and `null`
- generic PDF keywords
- token byte offsets
- explicit lexical errors for malformed input

### Object model

`src/ec_pdf_decoder/objects.py` provides typed representations for:

- names
- numbers
- strings and hexadecimal strings
- booleans and null
- arrays
- dictionaries
- indirect references such as `236 0 R`
- indirect objects
- stream objects

PDF byte strings are intentionally **not** decoded as UTF-8 by this layer.

### Recursive parser

`src/ec_pdf_decoder/parser.py` converts lexer tokens into the object model and currently handles:

- scalar values
- arrays
- dictionaries
- indirect references
- complete indirect-object definitions such as `6 0 obj ... endobj`

Stream byte extraction remains a separate responsibility because raw stream data must not be tokenized as ordinary PDF syntax.

## The important Bangla problem

A typical problematic extraction can produce incorrect characters even though Acrobat or a browser renders the intended Bangla text correctly.

This does **not** automatically mean the PDF is image-only. A PDF can contain selectable text whose glyph/CID mapping and Unicode mapping interact differently with different extraction implementations.

The project therefore follows the actual PDF data path:

```text
PDF bytes
   ↓
PDF objects / xref
   ↓
page resources
   ↓
content stream
   ↓
font resource
   ↓
CID / character codes
   ↓
ToUnicode CMap + embedded font information
   ↓
logical Unicode reconstruction
   ↓
Bangla text
   ↓
structured voter record
```

## Accuracy goal

The goal is not to promise a meaningless percentage such as "99% accurate" independent of the source data. The real goal is deterministic, testable decoding: for a PDF whose embedded CMap, font, and content information is sufficient to reconstruct the source text, the decoder should reproduce that text without OCR.

When a PDF contains ambiguous, incomplete, or deliberately non-standard mappings, the decoder should report the ambiguity instead of silently inventing characters.

## Development

Python 3.10+ is recommended.

Create a virtual environment on Windows:

```cmd
python -m venv .venv
.venv\Scripts\activate
```

Install the development dependencies used by the current implementation:

```cmd
pip install fonttools rich pytest
```

Run the test suite:

```cmd
python -m pytest -q
```

Compile-check the package directly when needed:

```cmd
python -m py_compile src\ec_pdf_decoder\lexer.py
python -m py_compile src\ec_pdf_decoder\objects.py
python -m py_compile src\ec_pdf_decoder\parser.py
```

## Repository workflow

The project is intentionally developed in small, reviewable commits. Before starting a new change:

```cmd
git pull
```

After tests pass:

```cmd
git status
git add .
git commit -m "Describe the change"
git push
```

## Security and privacy

Election/voter PDFs can contain sensitive personal information. Sample documents should remain private unless there is a deliberate reason to publish them. The repository is intended to remain private during development.

Do not commit credentials, access tokens, personal databases, or unrelated voter datasets.

## Project status

**Early development / reverse-engineering stage.**

The lexer, core object model, and recursive parser are being established first. The next major layers are robust indirect-object/document parsing, cross-reference handling, stream/filter decoding, `/ToUnicode` CMap parsing, embedded TrueType font inspection, PDF content-operator decoding, and finally Bangla-specific text reconstruction and structured voter extraction.

## License

License terms have not yet been finalized.

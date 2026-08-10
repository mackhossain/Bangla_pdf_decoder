# EC PDF Bangla Decoder

A local, deterministic decoder and manual reviewer for Bangladesh Election Commission (EC) voter-list PDFs that use embedded/subsetted Bangla TrueType fonts.

## Main workflow

Decode a page:

```cmd
python decoder.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3
```

Save decoded Unicode text:

```cmd
python decoder.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --text page3.txt
```

Save diagnostics:

```cmd
python decoder.py 261694_com_1267_female_without_photo_71_2025-11-24.pdf --page 3 --json page3.json
```

## Use from another Python program

The decoder can also be imported directly. The public `decode_pdf()` function uses the same decoding pipeline as the command-line program.

Normal decoding:

```python
import decoder

text = decoder.decode_pdf(
    "261825_com_463_male_without_photo_26_2025-11-24.pdf",
    page=13,
)

print(text)
```

Decoding with the same interactive manual review workflow:

```python
import decoder

text = decoder.decode_pdf(
    "261825_com_463_male_without_photo_26_2025-11-24.pdf",
    page=13,
    review=True,
)

print(text)
```

`review=True` is the Python equivalent of:

```cmd
python decoder.py 261825_com_463_male_without_photo_26_2025-11-24.pdf --page 13 --review
```

A `pathlib.Path` can also be supplied:

```python
from pathlib import Path
import decoder

text = decoder.decode_pdf(
    Path("261825_com_463_male_without_photo_26_2025-11-24.pdf"),
    page=13,
    review=True,
)
```

The CLI remains fully supported; importing `decoder` is an additional API and does not replace the command-line interface.

## Manual glyph review

When a page contains genuinely unresolved CID/GID values:

```cmd
python decoder.py 261825_com_463_male_without_photo_26_2025-11-24.pdf --page 1 --review
```

The production reviewer:

1. extracts the embedded Bangla TrueType font;
2. uses the PDF's own `ToUnicode` mappings first;
3. reuses confirmed mappings for the exact embedded-font fingerprint;
4. reuses unambiguous confirmed glyph fingerprints across different PDFs;
5. repairs already-known glyphs in the review line before creating the visual;
6. creates **one self-contained HTML review file** for the genuinely unresolved target;
7. renders the complete line from the exact embedded PDF glyph outlines and highlights the target CID/GID in red;
8. accepts an exact Unicode string entered in the console and saves it at confidence `1.0`;
9. deletes the temporary HTML automatically after a successful save.

Only genuinely unresolved glyphs remain as `⟦CID:n⟧` markers in the review text. A CID number alone is never treated as globally meaningful because subsetted PDF fonts can reuse CID/GID numbers for different glyphs.

### Review commands

```text
M = enter the exact Unicode character/string
N = move to the next unresolved glyph
S = skip the current glyph
Q = quit and keep all saved mappings
```

There is no AI/API requirement for the production review workflow.

## Persistent mapping data

`learned_glyph_map.json` is the main persistent knowledge base. Confirmed entries include the embedded-font identity, GID, Unicode text, glyph fingerprint, confidence, and source.

`custom_glyph_map.json` contains the existing/legacy deterministic PDF mappings and remains part of the decoding path.

Lookup is intentionally conservative:

```text
PDF ToUnicode / exact font mapping
        ↓
confirmed learned mapping for the same font
        ↓
unambiguous learned glyph fingerprint
        ↓
manual review only if still unresolved
```

The decoder does not silently guess an unknown glyph.

## Bengali text handling

The project includes Bengali logical-order restoration, NFC normalization, cleanup/corrections, and HarfBuzz-based shaping/candidate logic used by the decoding pipeline.

Relevant data files include:

```text
data/bangla_conjuncts.json
data/bangla_corrections.json
```

## Optional Unicode TTF generation

The repository also contains the optional TTF generator:

```cmd
python decoder.py --generate-ttf
```

It builds from confirmed mappings and a cached embedded Bangla TTF. If the source font has not yet been cached, first run the normal decoder once on an EC PDF so the embedded `FontFile2` is saved under `data/embedded_fonts/`.

The output is:

```text
EC_Bangla_Unicode.ttf
```

This is an optional utility; the normal PDF decoding and manual-review workflow does not depend on it.

## Low-level diagnostics

The repository retains low-level extraction/diagnostic utilities for troubleshooting unusual PDFs, including `decode_pdf.py`, `analyze_custom_context.py`, and the modules under `src/ec_pdf_decoder/`.

The normal production entry point is `decoder.py`.

## Generated and local files

Do not commit real voter PDFs, generated review HTML, extracted/generated TTFs, temporary text/JSON output, SVG/debug artifacts, Python caches, credentials, or API keys. Project-specific ignore rules are included in `.gitignore`.

## Installation

Python 3.10+ is recommended.

```cmd
python -m pip install -r requirements.txt
```

The runtime dependencies are intentionally limited to the packages used by the current decoder and shaping/TTF pipeline.

## Privacy

Election/voter PDFs can contain sensitive personal information. Keep real voter PDFs and generated personal-data output outside the Git repository.

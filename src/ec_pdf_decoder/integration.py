"""Integration helpers for inspecting real EC voter-list PDFs."""

from __future__ import annotations

from dataclasses import dataclass

from .cmap import CMap, parse_cmap
from .document import PDFDocument, PDFDocumentError
from .objects import PDFDictionary, PDFIndirectRef, PDFStream
from .streams import PDFStreamError, decode_stream


class IntegrationError(ValueError):
    """Raised when a PDF does not expose the expected font/CMap structure."""


@dataclass(frozen=True)
class CMapInspection:
    font_reference: PDFIndirectRef
    descendant_reference: PDFIndirectRef | None
    to_unicode_reference: PDFIndirectRef
    compressed_stream_length: int
    cmap_bytes: bytes
    cmap: CMap


def _as_ref(value, label: str) -> PDFIndirectRef:
    if not isinstance(value, PDFIndirectRef):
        raise IntegrationError(f"{label} is not an indirect reference: {value!r}")
    return value


def inspect_tounicode(path: str, font_object: int = 6) -> CMapInspection:
    """Resolve a Type0 font's ToUnicode stream and parse its CMap.

    This function deliberately does not decode page text. It only proves that
    the PDF's own ToUnicode mapping can be reached and parsed without OCR or
    hard-coded mappings.
    """
    try:
        document = PDFDocument.from_file(path)
        font = document.resolve((font_object, 0)).object.value
    except (PDFDocumentError, OSError) as exc:
        raise IntegrationError(str(exc)) from exc

    if not isinstance(font, PDFDictionary):
        raise IntegrationError(f"font object {font_object} is not a dictionary")

    to_unicode = _as_ref(font.get(b"ToUnicode"), "/ToUnicode")
    descendant_reference = None
    descendants = font.get(b"DescendantFonts")
    if descendants is not None:
        try:
            descendant_reference = descendants[0]
        except (IndexError, TypeError, KeyError):
            raise IntegrationError("/DescendantFonts is not a usable array")
        descendant_reference = _as_ref(descendant_reference, "/DescendantFonts[0]")

    try:
        stream = document.resolve_stream(to_unicode)
        cmap_bytes = decode_stream(stream)
        cmap = parse_cmap(cmap_bytes)
    except (PDFDocumentError, PDFStreamError, ValueError) as exc:
        raise IntegrationError(f"failed to resolve /ToUnicode {to_unicode}: {exc}") from exc

    return CMapInspection(
        font_reference=PDFIndirectRef(font_object, 0),
        descendant_reference=descendant_reference,
        to_unicode_reference=to_unicode,
        compressed_stream_length=len(stream.data),
        cmap_bytes=cmap_bytes,
        cmap=cmap,
    )


def inspect_summary(path: str, font_object: int = 6) -> dict[str, object]:
    """Return JSON-friendly diagnostic values for a real PDF."""
    result = inspect_tounicode(path, font_object)
    return {
        "font": f"{result.font_reference.object_number} {result.font_reference.generation} R",
        "descendant": (
            None
            if result.descendant_reference is None
            else f"{result.descendant_reference.object_number} {result.descendant_reference.generation} R"
        ),
        "to_unicode": f"{result.to_unicode_reference.object_number} {result.to_unicode_reference.generation} R",
        "compressed_stream_length": result.compressed_stream_length,
        "cmap_bytes": len(result.cmap_bytes),
        "codespaces": [
            {"start": x.start, "end": x.end, "width": x.width}
            for x in result.cmap.codespaces
        ],
        "mapping_count": len(result.cmap.mappings),
        "sample_mappings": [
            {"code": code, "unicode": list(result.cmap.mappings[code])}
            for code in sorted(result.cmap.mappings)[:10]
        ],
    }


__all__ = ["CMapInspection", "IntegrationError", "inspect_summary", "inspect_tounicode"]

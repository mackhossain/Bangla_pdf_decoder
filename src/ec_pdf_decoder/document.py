"""PDF document access built on the lexer, parser, and xref layers."""

from __future__ import annotations

from dataclasses import dataclass

from .objects import PDFDictionary, PDFIndirectObject, PDFIndirectRef, PDFStream
from .parser import PDFParseError, PDFParser
from .xref import XRefError, XRefTable, build_xref_table


class PDFDocumentError(ValueError):
    """Raised when a PDF document cannot be resolved safely."""


@dataclass(frozen=True)
class ResolvedObject:
    reference: PDFIndirectRef
    object: PDFIndirectObject
    offset: int


class PDFDocument:
    """Byte-backed PDF document with lazy indirect-object resolution."""

    def __init__(self, data: bytes | bytearray | memoryview):
        self.data = bytes(data)
        if not self.data.startswith(b"%PDF-"):
            raise PDFDocumentError("file does not start with a PDF header")
        try:
            self.xref: XRefTable = build_xref_table(self.data)
        except XRefError as exc:
            raise PDFDocumentError(str(exc)) from exc
        self._cache: dict[tuple[int, int], ResolvedObject] = {}

    @classmethod
    def from_file(cls, path: str) -> "PDFDocument":
        with open(path, "rb") as handle:
            return cls(handle.read())

    @property
    def startxref(self) -> int:
        return self.xref.trailer.startxref

    @property
    def trailer_bytes(self) -> bytes:
        return self.xref.trailer.raw

    def object_offset(self, object_number: int, generation: int = 0) -> int:
        try:
            return self.xref.require(object_number, generation).offset
        except XRefError as exc:
            raise PDFDocumentError(str(exc)) from exc

    def resolve(self, reference: PDFIndirectRef | tuple[int, int]) -> ResolvedObject:
        if isinstance(reference, PDFIndirectRef):
            object_number = reference.object_number
            generation = reference.generation
        else:
            object_number, generation = reference
            reference = PDFIndirectRef(object_number, generation)

        key = (object_number, generation)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        offset = self.object_offset(object_number, generation)
        # Parse only the indirect-object prefix/value. We locate the exact
        # endobj marker with PDF token boundaries rather than using a raw
        # substring search, so a string containing the bytes "endobj" cannot
        # terminate the object accidentally.
        parser = PDFParser(self.data[offset:])
        try:
            obj = parser.parse_indirect_object()
        except (PDFParseError, ValueError) as exc:
            raise PDFDocumentError(
                f"failed to parse object {object_number} {generation} R at offset {offset}: {exc}"
            ) from exc

        if obj.object_number != object_number or obj.generation != generation:
            raise PDFDocumentError(
                f"xref/object mismatch: requested {object_number} {generation} R, "
                f"found {obj.object_number} {obj.generation} R at offset {offset}"
            )

        result = ResolvedObject(reference, obj, offset)
        self._cache[key] = result
        return result

    def resolve_value(self, reference: PDFIndirectRef | tuple[int, int]):
        return self.resolve(reference).object.value

    def resolve_stream(self, reference: PDFIndirectRef | tuple[int, int]) -> PDFStream:
        value = self.resolve_value(reference)
        if not isinstance(value, PDFStream):
            raise PDFDocumentError(f"{reference!s} is not a stream object")
        return value

    def resolve_dictionary(self, reference: PDFIndirectRef | tuple[int, int]) -> PDFDictionary:
        value = self.resolve_value(reference)
        if not isinstance(value, PDFDictionary):
            raise PDFDocumentError(f"{reference!s} is not a dictionary object")
        return value


def inspect_pdf(path: str) -> dict[str, object]:
    """Return a small diagnostic snapshot without decoding text or fonts."""
    document = PDFDocument.from_file(path)
    snapshot: dict[str, object] = {
        "path": path,
        "size": len(document.data),
        "startxref": document.startxref,
        "trailer": document.trailer_bytes,
    }
    for object_number in (6, 235, 236):
        try:
            resolved = document.resolve((object_number, 0))
        except PDFDocumentError as exc:
            snapshot[f"object_{object_number}"] = f"ERROR: {exc}"
        else:
            snapshot[f"object_{object_number}"] = resolved.object.value
    return snapshot


__all__ = ["PDFDocument", "PDFDocumentError", "ResolvedObject", "inspect_pdf"]

"""PDF document access built on the lexer, parser, and xref layers."""

from __future__ import annotations

from dataclasses import dataclass

from .lexer import PDFLexer, TokenType
from .objects import PDFDictionary, PDFIndirectObject, PDFIndirectRef, PDFNumber, PDFStream
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

    @staticmethod
    def _stream_data_start(data: bytes, keyword_end: int) -> int:
        """Return the first byte after the required EOL following ``stream``."""
        position = keyword_end
        if data[position:position + 2] == b"\r\n":
            return position + 2
        if data[position:position + 1] in (b"\n", b"\r"):
            return position + 1
        raise PDFDocumentError("PDF stream keyword is not followed by an EOL")

    @staticmethod
    def _direct_stream_length(dictionary: PDFDictionary) -> int:
        value = dictionary.get(b"Length")
        if not isinstance(value, PDFNumber) or not isinstance(value.value, int):
            raise PDFDocumentError(
                "stream /Length must be a direct integer at this layer; "
                "indirect /Length resolution is not implemented yet"
            )
        if value.value < 0:
            raise PDFDocumentError("stream /Length cannot be negative")
        return value.value

    def _parse_resolved_object(self, offset: int, object_number: int, generation: int) -> PDFIndirectObject:
        """Parse an indirect object, including raw stream bytes when present.

        The ordinary value parser deliberately does not consume stream bytes.
        A stream is binary data, so it must be sliced from the original PDF
        using the dictionary's exact /Length before token parsing continues.
        """
        parser = PDFParser(self.data[offset:])
        try:
            first = parser.tokens.expect(TokenType.NUMBER)
            gen = parser.tokens.expect(TokenType.NUMBER)
            if not isinstance(first.value, int) or not isinstance(gen.value, int):
                raise PDFParseError("indirect object header must contain integer numbers")
            marker = parser.tokens.expect(TokenType.KEYWORD)
            if marker.value != b"obj":
                raise PDFParseError(f"expected obj, got {marker.value!r}")

            value = parser.parse_value()
            next_token = parser.tokens.peek()

            if next_token.type is TokenType.KEYWORD and next_token.value == b"stream":
                if not isinstance(value, PDFDictionary):
                    raise PDFParseError("stream object must begin with a dictionary")

                stream_start = self._stream_data_start(self.data, offset + next_token.end)
                length = self._direct_stream_length(value)
                stream_end = stream_start + length
                if stream_end > len(self.data):
                    raise PDFParseError("stream /Length extends beyond end of file")

                raw_stream = self.data[stream_start:stream_end]
                tail = self.data[stream_end:]
                tail_parser = PDFParser(tail)
                tail_parser.tokens.expect(TokenType.KEYWORD, b"endstream")
                tail_parser.tokens.expect(TokenType.KEYWORD, b"endobj")
                value = PDFStream(value, raw_stream)
            else:
                parser.tokens.expect(TokenType.KEYWORD, b"endobj")

            return PDFIndirectObject(first.value, gen.value, value)
        except (PDFParseError, ValueError) as exc:
            raise PDFDocumentError(
                f"failed to parse object {object_number} {generation} R at offset {offset}: {exc}"
            ) from exc

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
        obj = self._parse_resolved_object(offset, object_number, generation)

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

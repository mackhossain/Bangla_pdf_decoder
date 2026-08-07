"""Recursive parser for PDF objects and indirect-object definitions.

The parser consumes :class:`PDFLexer` tokens and produces the value classes in
``objects.py``.  It intentionally does not parse xref tables or streams from
the file; those are handled by the document layer later.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from .lexer import PDFLexer, PDFLexError, Token, TokenType
from .objects import (
    PDFArray,
    PDFBoolean,
    PDFDictionary,
    PDFHexString,
    PDFIndirectObject,
    PDFIndirectRef,
    PDFName,
    PDFNumber,
    PDFStream,
    PDFString,
    PDF_NULL,
    PDFObjectError,
)


class PDFParseError(ValueError):
    """Raised when a token stream is not valid PDF object syntax."""


class TokenStream:
    """Small buffered token stream with arbitrary look-ahead."""

    def __init__(self, tokens: Iterator[Token] | Sequence[Token]):
        self._tokens = iter(tokens)
        self._buffer: list[Token] = []

    def peek(self, count: int = 0) -> Token:
        while len(self._buffer) <= count:
            try:
                self._buffer.append(next(self._tokens))
            except StopIteration:
                return Token(TokenType.EOF)
        return self._buffer[count]

    def pop(self) -> Token:
        if self._buffer:
            return self._buffer.pop(0)
        try:
            return next(self._tokens)
        except StopIteration:
            return Token(TokenType.EOF)

    def expect(self, token_type: TokenType, value=None) -> Token:
        token = self.pop()
        if token.type is not token_type or (value is not None and token.value != value):
            wanted = token_type.name if value is None else f"{token_type.name} {value!r}"
            raise PDFParseError(
                f"expected {wanted} at byte {token.start}, "
                f"got {token.type.name} {token.value!r}"
            )
        return token


class PDFParser:
    """Parse PDF values from bytes or an existing token stream."""

    def __init__(self, data: bytes | bytearray | memoryview | None = None, *, tokens=None):
        if data is not None and tokens is not None:
            raise TypeError("provide data or tokens, not both")
        if tokens is None:
            if data is None:
                raise TypeError("data or tokens is required")
            tokens = PDFLexer(data)
        self.tokens = TokenStream(iter(tokens))

    def parse(self):
        token = self.tokens.peek()
        if token.type is TokenType.EOF:
            raise PDFParseError("unexpected end of input")
        return self.parse_value()

    def parse_value(self):
        token = self.tokens.peek()

        if token.type is TokenType.NUMBER:
            return self._parse_number_or_reference()

        token = self.tokens.pop()

        if token.type is TokenType.NAME:
            return PDFName(token.value)
        if token.type is TokenType.LITERAL_STRING:
            return PDFString(token.value)
        if token.type is TokenType.HEX_STRING:
            return PDFHexString(token.value)
        if token.type is TokenType.TRUE:
            return PDFBoolean(True)
        if token.type is TokenType.FALSE:
            return PDFBoolean(False)
        if token.type is TokenType.NULL:
            return PDF_NULL
        if token.type is TokenType.ARRAY_START:
            return self._parse_array()
        if token.type is TokenType.DICT_START:
            return self._parse_dictionary()

        raise PDFParseError(
            f"unexpected {token.type.name} {token.value!r} at byte {token.start}"
        )

    def _parse_number_or_reference(self):
        first = self.tokens.pop()
        assert first.type is TokenType.NUMBER

        # PDF indirect references are exactly: integer integer R.
        if isinstance(first.value, int):
            second = self.tokens.peek()
            third = self.tokens.peek(1)
            if (
                second.type is TokenType.NUMBER
                and isinstance(second.value, int)
                and third.type is TokenType.KEYWORD
                and third.value == b"R"
            ):
                self.tokens.pop()
                self.tokens.pop()
                return PDFIndirectRef(first.value, second.value)

        return PDFNumber(first.value)

    def _parse_array(self) -> PDFArray:
        values = []
        while True:
            token = self.tokens.peek()
            if token.type is TokenType.EOF:
                raise PDFParseError("unterminated array")
            if token.type is TokenType.ARRAY_END:
                self.tokens.pop()
                return PDFArray(tuple(values))
            if token.type is TokenType.COMMENT:
                self.tokens.pop()
                continue
            values.append(self.parse_value())

    def _parse_dictionary(self) -> PDFDictionary:
        entries = []
        while True:
            token = self.tokens.peek()
            if token.type is TokenType.EOF:
                raise PDFParseError("unterminated dictionary")
            if token.type is TokenType.DICT_END:
                self.tokens.pop()
                break
            if token.type is TokenType.COMMENT:
                self.tokens.pop()
                continue
            if token.type is not TokenType.NAME:
                raise PDFParseError(
                    f"dictionary key must be a name at byte {token.start}; "
                    f"got {token.type.name}"
                )
            key = PDFName(self.tokens.pop().value)
            value = self.parse_value()
            entries.append((key, value))

        try:
            return PDFDictionary(tuple(entries))
        except PDFObjectError as exc:
            raise PDFParseError(str(exc)) from exc

    def parse_indirect_object(self) -> PDFIndirectObject:
        """Parse ``objnum generation obj value endobj``."""
        first = self.tokens.expect(TokenType.NUMBER)
        generation = self.tokens.expect(TokenType.NUMBER)

        if not isinstance(first.value, int) or not isinstance(generation.value, int):
            raise PDFParseError("indirect object header must contain integer numbers")

        marker = self.tokens.expect(TokenType.KEYWORD)
        if marker.value != b"obj":
            raise PDFParseError(
                f"expected obj at byte {marker.start}, got {marker.value!r}"
            )

        value = self.parse_value()

        # A stream is represented by a dictionary followed by the stream
        # keyword and raw stream bytes.  Tokenizing raw stream bytes as PDF
        # syntax would be incorrect, so this low-level parser intentionally
        # leaves stream assembly to the stream/document parser.
        end = self.tokens.peek()
        if end.type is TokenType.KEYWORD and end.value == b"endobj":
            self.tokens.pop()
        else:
            raise PDFParseError(
                f"expected endobj after indirect object at byte {end.start}"
            )

        return PDFIndirectObject(first.value, generation.value, value)


def parse_value(data: bytes | bytearray | memoryview):
    """Parse exactly one PDF value from ``data``."""
    parser = PDFParser(data)
    value = parser.parse()
    token = parser.tokens.peek()
    if token.type is not TokenType.EOF:
        raise PDFParseError(
            f"trailing token {token.type.name} {token.value!r} at byte {token.start}"
        )
    return value


def parse_indirect_object(data: bytes | bytearray | memoryview) -> PDFIndirectObject:
    """Parse one complete indirect object from ``data``."""
    parser = PDFParser(data)
    return parser.parse_indirect_object()


__all__ = [
    "PDFParseError",
    "PDFParser",
    "TokenStream",
    "parse_indirect_object",
    "parse_value",
]

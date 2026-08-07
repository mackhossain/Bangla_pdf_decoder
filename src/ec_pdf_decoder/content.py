"""Tokenize and extract raw text-showing operations from PDF content streams."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator


class ContentSyntaxError(ValueError):
    """Raised when a PDF content stream cannot be tokenized safely."""


@dataclass(frozen=True)
class PDFName:
    value: bytes


@dataclass(frozen=True)
class PDFString:
    value: bytes


@dataclass(frozen=True)
class PDFHexString:
    value: bytes


@dataclass(frozen=True)
class PDFNumber:
    value: int | float


@dataclass(frozen=True)
class PDFOperator:
    value: bytes


@dataclass(frozen=True)
class TextShow:
    operator: bytes
    value: bytes | tuple[bytes | float | int, ...]


_WHITESPACE = b"\x00\t\n\f\r "
_DELIMS = b"()<>[]{}/%"


def _is_space(byte: int) -> bool:
    return byte in _WHITESPACE


def _is_delim(byte: int) -> bool:
    return byte in _DELIMS


def _hex_decode(raw: bytes) -> bytes:
    compact = b"".join(raw.split())
    if len(compact) % 2:
        compact += b"0"
    try:
        return bytes.fromhex(compact.decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ContentSyntaxError("invalid PDF hexadecimal string") from exc


def _literal_decode(raw: bytes, start: int) -> tuple[bytes, int]:
    out = bytearray()
    i = start + 1
    depth = 1
    n = len(raw)
    while i < n:
        b = raw[i]
        if b == ord("("):
            depth += 1
            out.append(b)
            i += 1
            continue
        if b == ord(")"):
            depth -= 1
            if depth == 0:
                return bytes(out), i + 1
            out.append(b)
            i += 1
            continue
        if b == ord("\\"):
            i += 1
            if i >= n:
                raise ContentSyntaxError("unterminated escape in PDF string")
            esc = raw[i]
            simple = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
            if esc in simple:
                out.append(simple[esc])
                i += 1
                continue
            if esc in (ord("\\"), ord("("), ord(")")):
                out.append(esc)
                i += 1
                continue
            if esc in b"\r\n":
                if esc == ord("\r") and i + 1 < n and raw[i + 1] == ord("\n"):
                    i += 1
                i += 1
                continue
            if 48 <= esc <= 55:
                digits = bytearray([esc])
                i += 1
                while len(digits) < 3 and i < n and 48 <= raw[i] <= 55:
                    digits.append(raw[i])
                    i += 1
                out.append(int(bytes(digits), 8) & 0xFF)
                continue
            # PDF defines an unrecognised escape as the escaped character
            # itself. This deliberately consumes exactly one byte.
            out.append(esc)
            i += 1
            continue
        out.append(b)
        i += 1
    raise ContentSyntaxError("unterminated PDF literal string")


def tokenize(data: bytes | bytearray | memoryview) -> Iterator[object]:
    """Yield PDF content-stream operands and operators."""
    raw = bytes(data)
    i = 0
    n = len(raw)
    while i < n:
        if _is_space(raw[i]):
            i += 1
            continue
        if raw[i] == ord("%"):
            i += 1
            while i < n and raw[i] not in b"\r\n":
                i += 1
            continue
        if raw[i] == ord("("):
            value, i = _literal_decode(raw, i)
            yield PDFString(value)
            continue
        if raw[i] == ord("<") and i + 1 < n and raw[i + 1] == ord("<"):
            yield PDFOperator(b"<<")
            i += 2
            continue
        if raw[i] == ord(">") and i + 1 < n and raw[i + 1] == ord(">"):
            yield PDFOperator(b">>")
            i += 2
            continue
        if raw[i] == ord("<"):
            end = raw.find(b">", i + 1)
            if end < 0:
                raise ContentSyntaxError("unterminated PDF hexadecimal string")
            yield PDFHexString(_hex_decode(raw[i + 1:end]))
            i = end + 1
            continue
        if raw[i] == ord("/"):
            i += 1
            start = i
            while i < n and not _is_space(raw[i]) and not _is_delim(raw[i]):
                i += 1
            yield PDFName(raw[start:i])
            continue
        if raw[i] in (ord("["), ord("]")):
            yield PDFOperator(bytes((raw[i],)))
            i += 1
            continue

        start = i
        while i < n and not _is_space(raw[i]) and not _is_delim(raw[i]):
            i += 1
        token = raw[start:i]
        if not token:
            raise ContentSyntaxError(f"unexpected byte at offset {i}")
        try:
            text = token.decode("ascii")
        except UnicodeDecodeError:
            yield PDFOperator(token)
            continue

        stripped = text.lstrip("+-")
        if stripped.isdigit() and stripped:
            yield PDFNumber(int(text))
            continue
        if any(ch in text for ch in ".eE"):
            try:
                yield PDFNumber(float(text))
                continue
            except ValueError:
                pass
        yield PDFOperator(token)


def _text_bytes(value: object) -> bytes:
    if isinstance(value, PDFString):
        return value.value
    if isinstance(value, PDFHexString):
        return value.value
    raise ContentSyntaxError("text operand is not a string")


def extract_text_show_operations(data: bytes | bytearray | memoryview) -> list[TextShow]:
    """Extract Tj/TJ/'/\" operands while preserving raw font bytes."""
    stack: list[object] = []
    array_stack: list[list[bytes | float | int]] = []
    result: list[TextShow] = []

    for token in tokenize(data):
        if isinstance(token, PDFOperator) and token.value == b"[":
            array_stack.append([])
            continue

        if isinstance(token, PDFOperator) and token.value == b"]":
            if not array_stack:
                raise ContentSyntaxError("unmatched TJ array terminator")
            values = tuple(array_stack.pop())
            if array_stack:
                raise ContentSyntaxError("nested arrays are not valid TJ operands")
            stack.append(values)
            continue

        if array_stack:
            if isinstance(token, PDFString):
                array_stack[-1].append(token.value)
            elif isinstance(token, PDFHexString):
                array_stack[-1].append(token.value)
            elif isinstance(token, PDFNumber):
                array_stack[-1].append(token.value)
            else:
                raise ContentSyntaxError("invalid item inside TJ array")
            continue

        if not isinstance(token, PDFOperator):
            stack.append(token)
            continue

        op = token.value
        if op == b"Tj":
            if not stack:
                raise ContentSyntaxError("Tj has no operand")
            result.append(TextShow(op, _text_bytes(stack.pop())))
            stack.clear()
            continue

        if op == b"TJ":
            if not stack:
                raise ContentSyntaxError("TJ has no operand")
            value = stack.pop()
            if not isinstance(value, tuple):
                raise ContentSyntaxError("TJ operand is not an array")
            result.append(TextShow(op, value))
            stack.clear()
            continue

        if op in (b"'", b'"'):
            if not stack:
                raise ContentSyntaxError(f"{op!r} has no text operand")
            result.append(TextShow(op, _text_bytes(stack.pop())))
            stack.clear()
            continue

        stack.append(token)

    if array_stack:
        raise ContentSyntaxError("unterminated TJ array")
    return result


__all__ = [
    "ContentSyntaxError",
    "PDFHexString",
    "PDFName",
    "PDFNumber",
    "PDFOperator",
    "PDFString",
    "TextShow",
    "extract_text_show_operations",
    "tokenize",
]

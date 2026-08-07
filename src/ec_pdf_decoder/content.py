"""Tokenize and extract text-showing operations from PDF content streams."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator
import re


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
_INLINE_EI = re.compile(rb"[\x00\t\n\f\r ]EI(?=[\x00\t\n\f\r ])")


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
            out.append(esc)
            i += 1
            continue
        out.append(b)
        i += 1
    raise ContentSyntaxError("unterminated PDF literal string")


def _skip_inline_image(raw: bytes, start: int) -> int:
    n = len(raw)
    id_match = re.search(rb"[\x00\t\n\f\r ]ID(?:[\x00\t\n\f\r ])", raw[start:])
    if not id_match:
        raise ContentSyntaxError("inline image has no ID marker")
    data_start = start + id_match.end()
    while data_start < n and _is_space(raw[data_start]):
        data_start += 1
    end_match = _INLINE_EI.search(raw, data_start)
    if not end_match:
        raise ContentSyntaxError("inline image has no EI marker")
    return end_match.end()


def tokenize(data: bytes | bytearray | memoryview) -> Iterator[object]:
    """Yield PDF content-stream tokens."""
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
        if token == b"BI":
            i = _skip_inline_image(raw, i)


def _text_bytes(value: object) -> bytes:
    if isinstance(value, PDFString):
        return value.value
    if isinstance(value, PDFHexString):
        return value.value
    raise ContentSyntaxError("text operand is not a string")


def extract_text_show_operations(data: bytes | bytearray | memoryview) -> list[TextShow]:
    """Extract text-showing operations without discarding later operations."""
    result: list[TextShow] = []
    array_stack: list[list[bytes | float | int]] = []
    pending: list[object] = []

    def emit_simple(op: bytes) -> None:
        if not pending:
            return
        value = pending[-1]
        if op == b"Tj" or op in (b"'", b'"'):
            if isinstance(value, (PDFString, PDFHexString)):
                result.append(TextShow(op, _text_bytes(value)))
        elif op == b"TJ" and isinstance(value, tuple):
            result.append(TextShow(op, value))
        pending.clear()

    tokens = tokenize(data)
    while True:
        try:
            token = next(tokens)
        except StopIteration:
            break
        except ContentSyntaxError:
            # A malformed non-text construct must not erase text already found.
            # The caller can inspect the page with the diagnostic CLI.
            break

        if isinstance(token, PDFOperator) and token.value == b"[":
            if array_stack:
                # PDF content arrays are not nestable. Keep the outer state and
                # skip the malformed nested array until the next close bracket.
                continue
            array_stack.append([])
            continue

        if isinstance(token, PDFOperator) and token.value == b"]":
            if not array_stack:
                continue
            values = tuple(array_stack.pop())
            pending.append(values)
            continue

        if array_stack:
            if isinstance(token, PDFString):
                array_stack[-1].append(token.value)
            elif isinstance(token, PDFHexString):
                array_stack[-1].append(token.value)
            elif isinstance(token, PDFNumber):
                array_stack[-1].append(token.value)
            else:
                # An unexpected operator closes the malformed array state so
                # subsequent text operations can still be recovered.
                array_stack.clear()
            continue

        if not isinstance(token, PDFOperator):
            pending.append(token)
            continue

        emit_simple(token.value)

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

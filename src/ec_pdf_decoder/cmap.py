"""Parser for PDF ToUnicode CMap streams."""

from __future__ import annotations

from dataclasses import dataclass
import re


class CMapError(ValueError):
    """Raised when a ToUnicode CMap is malformed."""


@dataclass(frozen=True)
class CodeSpaceRange:
    start: int
    end: int
    width: int


@dataclass(frozen=True)
class CMap:
    codespaces: tuple[CodeSpaceRange, ...]
    mappings: dict[int, tuple[int, ...]]

    def lookup(self, code: int) -> tuple[int, ...] | None:
        return self.mappings.get(code)

    def decode_code(self, code: int) -> str:
        value = self.lookup(code)
        if value is None:
            return "\ufffd"
        try:
            return "".join(chr(cp) for cp in value)
        except ValueError as exc:
            raise CMapError(f"invalid Unicode mapping for code {code:#x}") from exc


_HEX = re.compile(rb"<([0-9A-Fa-f]+)>")


def _hex_value(token: bytes) -> int:
    match = _HEX.fullmatch(token)
    if not match:
        raise CMapError(f"invalid hexadecimal token: {token!r}")
    digits = match.group(1)
    if len(digits) % 2:
        raise CMapError(f"odd-length hexadecimal token: {token!r}")
    return int(digits, 16)


def _hex_bytes(token: bytes) -> bytes:
    match = _HEX.fullmatch(token)
    if not match:
        raise CMapError(f"invalid hexadecimal token: {token!r}")
    digits = match.group(1)
    if len(digits) % 2:
        raise CMapError(f"odd-length hexadecimal token: {token!r}")
    return bytes.fromhex(digits.decode("ascii"))


def _unicode_values(token: bytes) -> tuple[int, ...]:
    raw = _hex_bytes(token)
    if len(raw) == 0 or len(raw) % 2:
        raise CMapError(f"invalid Unicode destination: {token!r}")
    values: list[int] = []
    for pos in range(0, len(raw), 2):
        values.append(int.from_bytes(raw[pos:pos + 2], "big"))
    return tuple(values)


def _tokens(data: bytes) -> list[bytes]:
    # Remove comments first. CMap syntax is ASCII and the stream is kept as
    # bytes throughout this parser.
    cleaned = re.sub(rb"%[^\r\n]*(?:\r?\n|$)", b"\n", data)
    return re.findall(rb"<[^>]*>|\S+", cleaned)


def _next_hex(tokens: list[bytes], index: int) -> tuple[bytes, int]:
    if index >= len(tokens):
        raise CMapError("unexpected end of CMap")
    token = tokens[index]
    if not _HEX.fullmatch(token):
        raise CMapError(f"expected hexadecimal token, got {token!r}")
    return token, index + 1


def parse_cmap(data: bytes | bytearray | memoryview) -> CMap:
    """Parse codespace, bfchar, and bfrange mappings from a ToUnicode CMap.

    Supported source forms are the standard PDF CMap operators:
    ``begincodespacerange``, ``beginbfchar`` and ``beginbfrange``. Both a
    single destination and an array of destinations in a bfrange are handled.
    """
    tokens = _tokens(bytes(data))
    codespaces: list[CodeSpaceRange] = []
    mappings: dict[int, tuple[int, ...]] = {}
    i = 0

    while i < len(tokens):
        token = tokens[i]
        if token == b"begincodespacerange":
            i += 1
            while i < len(tokens) and tokens[i] != b"endcodespacerange":
                start_token, i = _next_hex(tokens, i)
                end_token, i = _next_hex(tokens, i)
                start_raw = _hex_bytes(start_token)
                end_raw = _hex_bytes(end_token)
                if len(start_raw) != len(end_raw) or not start_raw:
                    raise CMapError("codespace endpoints must have equal non-zero width")
                start = int.from_bytes(start_raw, "big")
                end = int.from_bytes(end_raw, "big")
                if start > end:
                    raise CMapError("codespace start is greater than end")
                codespaces.append(CodeSpaceRange(start, end, len(start_raw)))
            if i >= len(tokens):
                raise CMapError("unterminated codespacerange")
            i += 1
            continue

        if token == b"beginbfchar":
            i += 1
            while i < len(tokens) and tokens[i] != b"endbfchar":
                source, i = _next_hex(tokens, i)
                destination, i = _next_hex(tokens, i)
                mappings[_hex_value(source)] = _unicode_values(destination)
            if i >= len(tokens):
                raise CMapError("unterminated bfchar")
            i += 1
            continue

        if token == b"beginbfrange":
            i += 1
            while i < len(tokens) and tokens[i] != b"endbfrange":
                start_token, i = _next_hex(tokens, i)
                end_token, i = _next_hex(tokens, i)
                start_raw = _hex_bytes(start_token)
                end_raw = _hex_bytes(end_token)
                if len(start_raw) != len(end_raw) or not start_raw:
                    raise CMapError("bfrange endpoints must have equal non-zero width")
                start = int.from_bytes(start_raw, "big")
                end = int.from_bytes(end_raw, "big")
                if start > end:
                    raise CMapError("bfrange start is greater than end")

                if i >= len(tokens):
                    raise CMapError("missing bfrange destination")
                destination = tokens[i]
                i += 1

                if destination.startswith(b"["):
                    # Tokenizer keeps '[' and ']' separate, so this branch is
                    # retained only for malformed/non-standard input.
                    raise CMapError("unexpected bfrange array syntax")

                if destination.startswith(b"<"):
                    base = list(_unicode_values(destination))
                    if end - start + 1 > 1 and len(base) == 0:
                        raise CMapError("empty bfrange destination")
                    for code in range(start, end + 1):
                        values = list(base)
                        if not values:
                            raise CMapError("empty bfrange destination")
                        # Standard bfrange semantics increment the final
                        # UTF-16 code unit for each source code.
                        delta = code - start
                        last = values[-1] + delta
                        if last > 0xFFFF:
                            raise CMapError("bfrange Unicode destination overflow")
                        values[-1] = last
                        mappings[code] = tuple(values)
                elif destination == b"[":
                    for code in range(start, end + 1):
                        if i >= len(tokens):
                            raise CMapError("unterminated bfrange destination array")
                        value = tokens[i]
                        i += 1
                        if value == b"]":
                            raise CMapError("too few bfrange array destinations")
                        mappings[code] = _unicode_values(value)
                    if i >= len(tokens) or tokens[i] != b"]":
                        raise CMapError("unterminated bfrange destination array")
                    i += 1
                else:
                    raise CMapError(f"invalid bfrange destination: {destination!r}")

            if i >= len(tokens):
                raise CMapError("unterminated bfrange")
            i += 1
            continue

        i += 1

    if not codespaces:
        raise CMapError("CMap contains no codespace ranges")
    return CMap(tuple(codespaces), mappings)


__all__ = ["CMap", "CMapError", "CodeSpaceRange", "parse_cmap"]

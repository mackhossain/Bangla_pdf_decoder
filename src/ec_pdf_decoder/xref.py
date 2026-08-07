"""PDF cross-reference and trailer discovery.

The document layer needs reliable object offsets before it can resolve
references such as ``236 0 R``.  This module supports the traditional
plain-text xref table format first.  Compressed/object-stream xref support
will be added separately because it requires parsing the PDF object-stream
format.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator


class XRefError(ValueError):
    """Raised when a cross-reference structure is malformed or unsupported."""


@dataclass(frozen=True)
class XRefEntry:
    object_number: int
    generation: int
    offset: int
    in_use: bool


@dataclass(frozen=True)
class XRefSection:
    entries: tuple[XRefEntry, ...]
    offset: int


@dataclass(frozen=True)
class Trailer:
    raw: bytes
    startxref: int


@dataclass(frozen=True)
class XRefTable:
    entries: dict[tuple[int, int], XRefEntry]
    sections: tuple[XRefSection, ...]
    trailer: Trailer

    def get(self, object_number: int, generation: int = 0) -> XRefEntry | None:
        return self.entries.get((object_number, generation))

    def require(self, object_number: int, generation: int = 0) -> XRefEntry:
        entry = self.get(object_number, generation)
        if entry is None:
            raise XRefError(f"object {object_number} {generation} R is not in xref")
        if not entry.in_use:
            raise XRefError(f"object {object_number} {generation} R is free")
        return entry


def find_startxref(data: bytes | bytearray | memoryview) -> int:
    """Return the byte offset recorded by the final ``startxref`` marker."""
    raw = bytes(data)
    marker = b"startxref"
    position = raw.rfind(marker)
    if position < 0:
        raise XRefError("startxref marker not found")

    cursor = position + len(marker)
    while cursor < len(raw) and raw[cursor] in b" \t\r\n\f\x00":
        cursor += 1

    begin = cursor
    while cursor < len(raw) and 0x30 <= raw[cursor] <= 0x39:
        cursor += 1
    if begin == cursor:
        raise XRefError("startxref is not followed by a numeric offset")

    offset = int(raw[begin:cursor])
    if offset >= len(raw):
        raise XRefError(f"startxref offset {offset} is outside the file")
    return offset


def _read_line(data: bytes, position: int) -> tuple[bytes, int]:
    if position >= len(data):
        return b"", position
    end = data.find(b"\n", position)
    if end < 0:
        return data[position:], len(data)
    line = data[position:end]
    if line.endswith(b"\r"):
        line = line[:-1]
    return line, end + 1


def _skip_ws(data: bytes, position: int) -> int:
    while position < len(data) and data[position] in b" \t\r\n\f\x00":
        position += 1
    return position


def _parse_subsection_header(line: bytes) -> tuple[int, int] | None:
    parts = line.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def parse_xref_table(data: bytes | bytearray | memoryview, offset: int) -> XRefSection:
    """Parse one traditional ``xref`` section at ``offset``."""
    raw = bytes(data)
    if offset < 0 or offset >= len(raw):
        raise XRefError(f"xref offset {offset} is outside the file")

    position = _skip_ws(raw, offset)
    line, position = _read_line(raw, position)
    if line.strip() != b"xref":
        raise XRefError(
            f"expected traditional xref table at offset {offset}, got {line!r}"
        )

    entries: list[XRefEntry] = []

    while position < len(raw):
        position = _skip_ws(raw, position)
        line_start = position
        line, position = _read_line(raw, position)
        stripped = line.strip()

        if not stripped:
            continue
        if stripped == b"trailer":
            break

        header = _parse_subsection_header(stripped)
        if header is None:
            raise XRefError(f"invalid xref subsection header at {line_start}: {line!r}")
        first_object, count = header
        if first_object < 0 or count < 0:
            raise XRefError("negative xref subsection values")

        for index in range(count):
            entry_line, position = _read_line(raw, position)
            parts = entry_line.split()
            if len(parts) < 3:
                raise XRefError(
                    f"invalid xref entry {first_object + index}: {entry_line!r}"
                )
            try:
                entry_offset = int(parts[0])
                generation = int(parts[1])
            except ValueError as exc:
                raise XRefError(f"invalid numeric xref entry: {entry_line!r}") from exc

            state = parts[2]
            if state not in (b"n", b"f"):
                raise XRefError(f"invalid xref entry state: {entry_line!r}")

            entries.append(
                XRefEntry(
                    object_number=first_object + index,
                    generation=generation,
                    offset=entry_offset,
                    in_use=state == b"n",
                )
            )

    else:
        raise XRefError("xref table has no trailer marker")

    return XRefSection(tuple(entries), offset)


def extract_trailer_bytes(data: bytes | bytearray | memoryview, xref_offset: int) -> bytes:
    """Extract the bytes belonging to the traditional trailer dictionary.

    The parser intentionally returns the dictionary bytes rather than parsing
    them here.  The normal PDF parser remains the single implementation for
    PDF dictionary syntax.
    """
    raw = bytes(data)
    position = _skip_ws(raw, xref_offset)
    line, position = _read_line(raw, position)
    if line.strip() != b"xref":
        raise XRefError("trailer extraction currently requires a traditional xref table")

    while position < len(raw):
        position = _skip_ws(raw, position)
        line, position = _read_line(raw, position)
        if line.strip() == b"trailer":
            position = _skip_ws(raw, position)
            if position >= len(raw) or raw[position:position + 2] != b"<<":
                raise XRefError("trailer marker is not followed by a dictionary")

            end = raw.find(b">>", position + 2)
            if end < 0:
                raise XRefError("unterminated trailer dictionary")
            return raw[position:end + 2]

        header = _parse_subsection_header(line.strip())
        if header is None:
            raise XRefError(f"invalid xref subsection header: {line!r}")
        _, count = header
        for _ in range(count):
            _, position = _read_line(raw, position)

    raise XRefError("trailer marker not found")


def build_xref_table(data: bytes | bytearray | memoryview) -> XRefTable:
    """Build an xref table from a traditional xref section.

    The first implementation intentionally handles the common traditional
    table form.  If ``startxref`` points to an xref stream, ``XRefError`` is
    raised with a clear message rather than treating binary stream bytes as a
    text table.
    """
    raw = bytes(data)
    start = find_startxref(raw)

    if raw[_skip_ws(raw, start):_skip_ws(raw, start) + 4] != b"xref":
        raise XRefError(
            "startxref points to an xref stream or unsupported structure; "
            "xref streams are not implemented yet"
        )

    section = parse_xref_table(raw, start)
    trailer_bytes = extract_trailer_bytes(raw, start)

    entries: dict[tuple[int, int], XRefEntry] = {}
    for entry in section.entries:
        entries[(entry.object_number, entry.generation)] = entry

    return XRefTable(
        entries=entries,
        sections=(section,),
        trailer=Trailer(raw=trailer_bytes, startxref=start),
    )


__all__ = [
    "Trailer",
    "XRefEntry",
    "XRefError",
    "XRefSection",
    "XRefTable",
    "build_xref_table",
    "extract_trailer_bytes",
    "find_startxref",
    "parse_xref_table",
]

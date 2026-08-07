"""Core PDF object model.

This module contains the small, immutable-ish value objects used by the PDF
parser.  It deliberately does not know about files, xref tables, fonts, or
text decoding.  Those concerns belong to higher layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping, Sequence


class PDFObjectError(ValueError):
    """Raised when an invalid PDF object is constructed."""


@dataclass(frozen=True)
class PDFName:
    """A PDF name object, stored as decoded bytes.

    The lexer has already decoded ``#XX`` name escapes.  The bytes are kept
    unchanged so callers can choose the appropriate text encoding later.
    """

    value: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.value, bytes):
            raise TypeError("PDFName.value must be bytes")

    def __str__(self) -> str:
        try:
            return "/" + self.value.decode("latin-1")
        except UnicodeDecodeError:
            return "/" + self.value.decode("latin-1", errors="replace")

    def __bytes__(self) -> bytes:
        return self.value


@dataclass(frozen=True)
class PDFString:
    """Raw PDF string bytes.

    PDF strings are not assumed to be Unicode.  Interpretation can depend on
    a font, encoding, or document convention, so decoding is deferred.
    """

    value: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.value, bytes):
            raise TypeError("PDFString.value must be bytes")

    def __bytes__(self) -> bytes:
        return self.value


@dataclass(frozen=True)
class PDFHexString(PDFString):
    """Hexadecimal PDF string represented by its decoded bytes."""


@dataclass(frozen=True)
class PDFBoolean:
    value: bool

    def __bool__(self) -> bool:
        return self.value


@dataclass(frozen=True)
class PDFNull:
    """PDF null singleton value type."""


PDF_NULL = PDFNull()


@dataclass(frozen=True)
class PDFNumber:
    """PDF numeric value preserving whether it was integral."""

    value: int | float

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise TypeError("PDFNumber.value must be int or float")

    def __int__(self) -> int:
        return int(self.value)

    def __float__(self) -> float:
        return float(self.value)


@dataclass(frozen=True)
class PDFArray(Sequence[object]):
    items: tuple[object, ...] = ()

    def __iter__(self) -> Iterator[object]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index):
        return self.items[index]

    @classmethod
    def from_iterable(cls, values: Iterable[object]) -> "PDFArray":
        return cls(tuple(values))


@dataclass(frozen=True)
class PDFDictionary(Mapping[PDFName, object]):
    """PDF dictionary preserving PDFName keys.

    A tuple of pairs is used internally instead of a normal dict so the object
    remains deterministic and easy to inspect.  Duplicate keys are rejected;
    accepting them would hide malformed input and make downstream behavior
    ambiguous.
    """

    entries: tuple[tuple[PDFName, object], ...] = ()

    def __post_init__(self) -> None:
        seen: set[PDFName] = set()
        for key, _ in self.entries:
            if not isinstance(key, PDFName):
                raise TypeError("PDFDictionary keys must be PDFName instances")
            if key in seen:
                raise PDFObjectError(f"duplicate dictionary key: {key!s}")
            seen.add(key)

    def __getitem__(self, key: PDFName | bytes | str) -> object:
        wanted = _coerce_name(key)
        for name, value in self.entries:
            if name == wanted:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[PDFName]:
        for key, _ in self.entries:
            yield key

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, key: PDFName | bytes | str, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def contains(self, key: PDFName | bytes | str) -> bool:
        try:
            self[key]
        except KeyError:
            return False
        return True

    @classmethod
    def from_mapping(cls, values: Mapping[PDFName, object]) -> "PDFDictionary":
        return cls(tuple(values.items()))


@dataclass(frozen=True)
class PDFIndirectRef:
    """Reference to an indirect PDF object: ``object_number generation R``."""

    object_number: int
    generation: int

    def __post_init__(self) -> None:
        if self.object_number < 0:
            raise PDFObjectError("object number cannot be negative")
        if self.generation < 0:
            raise PDFObjectError("generation cannot be negative")

    def __str__(self) -> str:
        return f"{self.object_number} {self.generation} R"


@dataclass(frozen=True)
class PDFIndirectObject:
    """An indirect object definition and its parsed value."""

    object_number: int
    generation: int
    value: object

    def reference(self) -> PDFIndirectRef:
        return PDFIndirectRef(self.object_number, self.generation)


@dataclass(frozen=True)
class PDFStream:
    """A stream object: dictionary plus raw, still-encoded stream bytes."""

    dictionary: PDFDictionary
    data: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.dictionary, PDFDictionary):
            raise TypeError("stream dictionary must be PDFDictionary")
        if not isinstance(self.data, bytes):
            raise TypeError("stream data must be bytes")


def _coerce_name(value: PDFName | bytes | str) -> PDFName:
    if isinstance(value, PDFName):
        return value
    if isinstance(value, bytes):
        return PDFName(value)
    if isinstance(value, str):
        return PDFName(value.encode("latin-1"))
    raise TypeError("dictionary key must be PDFName, bytes, or str")


def pdf_name(value: PDFName | bytes | str) -> PDFName:
    """Construct a PDF name without requiring callers to spell the class."""
    return _coerce_name(value)


__all__ = [
    "PDFArray",
    "PDFBoolean",
    "PDFDictionary",
    "PDFHexString",
    "PDFIndirectObject",
    "PDFIndirectRef",
    "PDFName",
    "PDFNull",
    "PDFNumber",
    "PDFObjectError",
    "PDFStream",
    "PDFString",
    "PDF_NULL",
    "pdf_name",
]

"""Decode PDF stream objects while preserving raw bytes.

The first implementation supports the standard FlateDecode filter and
filter arrays containing only FlateDecode. More filters can be added without
changing the document/object layers.
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping, Sequence

from .objects import PDFArray, PDFDictionary, PDFName, PDFNumber, PDFStream


class PDFStreamError(ValueError):
    """Raised when a PDF stream cannot be decoded safely."""


def _name(value) -> bytes | None:
    if isinstance(value, PDFName):
        return value.value
    if isinstance(value, bytes):
        return value
    return None


def _dict_get(dictionary: PDFDictionary, key: bytes):
    return dictionary.get(key)


def _filters(dictionary: PDFDictionary) -> list[bytes]:
    value = _dict_get(dictionary, b"Filter")
    if value is None:
        return []
    if isinstance(value, PDFName):
        return [value.value]
    if isinstance(value, PDFArray):
        result: list[bytes] = []
        for item in value:
            name = _name(item)
            if name is None:
                raise PDFStreamError(f"invalid stream filter entry: {item!r}")
            result.append(name)
        return result
    raise PDFStreamError(f"invalid /Filter value: {value!r}")


def declared_length(dictionary: PDFDictionary) -> int | None:
    """Return /Length when it is a direct integer, otherwise None."""
    value = _dict_get(dictionary, b"Length")
    if isinstance(value, PDFNumber) and isinstance(value.value, int):
        if value.value < 0:
            raise PDFStreamError("negative stream /Length")
        return value.value
    return None


def flate_decode(data: bytes) -> bytes:
    """Decode one PDF FlateDecode stream using zlib."""
    try:
        return zlib.decompress(data)
    except zlib.error as exc:
        raise PDFStreamError(f"FlateDecode failed: {exc}") from exc


def decode_stream(stream: PDFStream) -> bytes:
    """Apply all declared filters in PDF order.

    Only FlateDecode is intentionally supported at this stage. Unsupported
    filters fail loudly instead of silently returning potentially corrupt text.
    """
    data = bytes(stream.data)
    for filter_name in _filters(stream.dictionary):
        if filter_name in (b"FlateDecode", b"Fl"):
            data = flate_decode(data)
        else:
            raise PDFStreamError(f"unsupported PDF stream filter: /{filter_name.decode('latin1')}")
    return data


def decode_stream_bytes(dictionary: PDFDictionary, data: bytes) -> bytes:
    """Decode raw stream bytes according to a PDF stream dictionary."""
    return decode_stream(PDFStream(dictionary, bytes(data)))


__all__ = [
    "PDFStreamError",
    "decode_stream",
    "decode_stream_bytes",
    "declared_length",
    "flate_decode",
]

"""Decode PDF text-showing operations through an embedded ToUnicode CMap."""

from __future__ import annotations

from .cmap import CMap
from .content import TextShow


def decode_text_show(operation: TextShow, cmap: CMap) -> str:
    """Decode one Tj/TJ operation, ignoring TJ positioning adjustments."""
    if operation.operator == b"TJ":
        parts: list[str] = []
        for value in operation.value:
            if isinstance(value, bytes):
                parts.append(cmap.decode_bytes(value))
        return "".join(parts)
    return cmap.decode_bytes(operation.value)


def decode_text_shows(operations: list[TextShow], cmap: CMap) -> list[str]:
    """Decode a sequence of raw text-showing operations to Unicode strings."""
    return [decode_text_show(operation, cmap) for operation in operations]


def decode_text(operations: list[TextShow], cmap: CMap) -> str:
    """Join decoded text-showing operations without inventing layout."""
    return "".join(decode_text_shows(operations, cmap))


__all__ = ["decode_text", "decode_text_show", "decode_text_shows"]

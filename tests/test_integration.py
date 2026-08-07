from pathlib import Path

import pytest

from src.ec_pdf_decoder.integration import IntegrationError, inspect_summary, inspect_tounicode
from src.ec_pdf_decoder.unicode_extract import extract_unicode_pages


PDF_NAME = "261694_com_1267_female_without_photo_71_2025-11-24.pdf"


def find_real_pdf() -> Path | None:
    candidates = [
        Path(PDF_NAME),
        Path(__file__).resolve().parents[1] / PDF_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def test_real_pdf_tounicode_when_available():
    path = find_real_pdf()
    if path is None:
        pytest.skip(f"real PDF {PDF_NAME!r} is not available in this checkout")

    result = inspect_tounicode(str(path), 6)
    assert result.font_reference.object_number == 6
    assert result.to_unicode_reference.object_number == 236
    assert result.descendant_reference is not None
    assert result.descendant_reference.object_number == 235
    assert len(result.cmap_bytes) > 0
    assert len(result.cmap.codespaces) >= 1
    assert len(result.cmap.mappings) > 0

    # Values read from the actual PDF's ToUnicode CMap. These assertions
    # verify that the integration path reaches the real CMap rather than a
    # synthetic or hard-coded mapping.
    assert result.cmap.lookup(0x001B) == (0x0981,)
    assert result.cmap.lookup(0x0038) == (0x09A3,)
    assert result.cmap.lookup(0x0078) == (0x25CC,)


def test_summary_is_json_friendly_when_available():
    path = find_real_pdf()
    if path is None:
        pytest.skip(f"real PDF {PDF_NAME!r} is not available in this checkout")
    summary = inspect_summary(str(path))
    assert summary["font"] == "6 0 R"
    assert summary["descendant"] == "235 0 R"
    assert summary["to_unicode"] == "236 0 R"
    assert summary["mapping_count"] > 0


def test_real_pdf_unicode_extraction_when_available():
    path = find_real_pdf()
    if path is None:
        pytest.skip(f"real PDF {PDF_NAME!r} is not available in this checkout")
    pages = extract_unicode_pages(path, 6)
    assert len(pages) == 73
    assert any(any("\u0980" <= ch <= "\u09ff" for ch in page) for page in pages)
    assert any(page.strip() for page in pages)


def test_missing_real_pdf_does_not_hide_integration_errors(tmp_path):
    missing = tmp_path / "missing.pdf"
    try:
        inspect_tounicode(str(missing), 6)
    except IntegrationError:
        pass
    else:
        raise AssertionError("expected IntegrationError for missing PDF")

from src.ec_pdf_decoder.document import PDFDocument, PDFDocumentError
from src.ec_pdf_decoder.objects import PDFDictionary, PDFIndirectRef


def sample_pdf() -> bytes:
    header = b"%PDF-1.4\n"
    object1 = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    object2 = b"2 0 obj\n<< /ToUnicode 3 0 R >>\nendobj\n"
    object3 = b"3 0 obj\n<< /Length 3 >>\nstream\nabc\nendstream\nendobj\n"
    body = object1 + object2 + object3
    offset1 = len(header)
    offset2 = offset1 + len(object1)
    offset3 = offset2 + len(object2)
    xref_offset = len(header) + len(body)
    xref = (
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        + f"{offset1:010d} 00000 n \n".encode()
        + f"{offset2:010d} 00000 n \n".encode()
        + f"{offset3:010d} 00000 n \n".encode()
        + b"trailer\n<< /Size 4 /Root 1 0 R >>\n"
        + b"startxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return header + body + xref


def test_resolve_indirect_object():
    document = PDFDocument(sample_pdf())
    resolved = document.resolve(PDFIndirectRef(2, 0))
    assert resolved.object.object_number == 2
    assert resolved.object.value[b"ToUnicode"] == PDFIndirectRef(3, 0)
    assert isinstance(resolved.object.value, PDFDictionary)


def test_object_offset_matches_xref():
    document = PDFDocument(sample_pdf())
    assert document.object_offset(1, 0) == sample_pdf().find(b"1 0 obj")


def test_cache_returns_same_resolved_object():
    document = PDFDocument(sample_pdf())
    first = document.resolve((1, 0))
    second = document.resolve((1, 0))
    assert first is second


def test_bad_header_rejected():
    try:
        PDFDocument(b"not a pdf")
    except PDFDocumentError as exc:
        assert "PDF header" in str(exc)
    else:
        raise AssertionError("expected PDFDocumentError")

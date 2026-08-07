from src.ec_pdf_decoder.xref import (
    XRefError,
    build_xref_table,
    find_startxref,
    parse_xref_table,
)


def sample_pdf() -> bytes:
    body = b"1 0 obj << /Type /Catalog >> endobj\n"
    offset = len(b"%PDF-1.4\n")
    xref_offset = len(b"%PDF-1.4\n") + len(body)
    xref = (
        b"xref\n"
        b"0 2\n"
        b"0000000000 65535 f \n"
        + f"{offset:010d} 00000 n \n".encode()
        + b"trailer\n"
        b"<< /Size 2 /Root 1 0 R >>\n"
        b"startxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return b"%PDF-1.4\n" + body + xref


def test_find_startxref():
    data = sample_pdf()
    assert find_startxref(data) == data.find(b"xref")


def test_parse_xref_table():
    data = sample_pdf()
    section = parse_xref_table(data, find_startxref(data))
    assert len(section.entries) == 2
    assert section.entries[0].object_number == 0
    assert section.entries[0].in_use is False
    assert section.entries[1].object_number == 1
    assert section.entries[1].in_use is True


def test_build_xref_table_and_lookup():
    table = build_xref_table(sample_pdf())
    entry = table.require(1, 0)
    assert entry.offset == len(b"%PDF-1.4\n")
    assert table.trailer.startxref == sample_pdf().find(b"xref")


def test_xref_stream_is_explicitly_rejected():
    data = b"%PDF-1.5\nstartxref\n20\n%%EOF\n" + b" " * 20
    try:
        build_xref_table(data)
    except XRefError as exc:
        assert "xref stream" in str(exc)
    else:
        raise AssertionError("expected xref stream rejection")

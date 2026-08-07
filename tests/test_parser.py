from src.ec_pdf_decoder.objects import (
    PDFArray,
    PDFDictionary,
    PDFIndirectRef,
    PDFName,
    PDFNumber,
    PDFString,
)
from src.ec_pdf_decoder.parser import PDFParseError, parse_indirect_object, parse_value


def test_parse_name():
    assert parse_value(b"/Bangla") == PDFName(b"Bangla")


def test_parse_name_hex_escape():
    assert parse_value(b"/A#20B") == PDFName(b"A B")


def test_parse_numbers():
    assert parse_value(b"123") == PDFNumber(123)
    assert parse_value(b"-1.25") == PDFNumber(-1.25)


def test_parse_reference():
    assert parse_value(b"236 0 R") == PDFIndirectRef(236, 0)


def test_parse_array():
    value = parse_value(b"[1 /Bangla (abc) 236 0 R]")
    assert isinstance(value, PDFArray)
    assert value[0] == PDFNumber(1)
    assert value[1] == PDFName(b"Bangla")
    assert value[2] == PDFString(b"abc")
    assert value[3] == PDFIndirectRef(236, 0)


def test_parse_dictionary():
    value = parse_value(b"<</BaseFont /CQCQVJ#2BBangla /ToUnicode 236 0 R>>")
    assert isinstance(value, PDFDictionary)
    assert value[b"BaseFont"] == PDFName(b"CQCQVJ+Bangla")
    assert value[b"ToUnicode"] == PDFIndirectRef(236, 0)


def test_parse_indirect_object():
    data = b"6 0 obj << /BaseFont /CQCQVJ+Bangla /ToUnicode 236 0 R >> endobj"
    obj = parse_indirect_object(data)
    assert obj.object_number == 6
    assert obj.generation == 0
    assert obj.value[b"BaseFont"] == PDFName(b"CQCQVJ+Bangla")
    assert obj.value[b"ToUnicode"] == PDFIndirectRef(236, 0)


def test_unterminated_array_fails():
    try:
        parse_value(b"[1 2")
    except PDFParseError:
        pass
    else:
        raise AssertionError("expected PDFParseError")

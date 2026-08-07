from src.ec_pdf_decoder.objects import (
    PDFArray,
    PDFBoolean,
    PDFDictionary,
    PDFHexString,
    PDFIndirectObject,
    PDFIndirectRef,
    PDFName,
    PDFNull,
    PDFNumber,
    PDFStream,
    PDFString,
    PDF_NULL,
)


def test_name_and_strings_keep_bytes():
    assert PDFName(b"Bangla").value == b"Bangla"
    assert PDFString(b"\\x00").value == b"\\x00"
    assert PDFHexString(b"\\x01\\x02").value == b"\\x01\\x02"


def test_array_is_sequence():
    value = PDFArray((PDFNumber(1), PDFNumber(2)))
    assert len(value) == 2
    assert int(value[0].value) == 1


def test_dictionary_lookup():
    value = PDFDictionary(((PDFName(b"Length"), PDFNumber(12)),))
    assert value[PDFName(b"Length")] == PDFNumber(12)
    assert value[b"Length"] == PDFNumber(12)
    assert value.get("Missing") is None


def test_indirect_reference():
    ref = PDFIndirectRef(236, 0)
    assert str(ref) == "236 0 R"
    obj = PDFIndirectObject(236, 0, PDFString(b"x"))
    assert obj.reference() == ref


def test_stream():
    dictionary = PDFDictionary(((PDFName(b"Length"), PDFNumber(3)),))
    stream = PDFStream(dictionary, b"abc")
    assert stream.data == b"abc"


def test_boolean_and_null():
    assert bool(PDFBoolean(True)) is True
    assert isinstance(PDF_NULL, PDFNull)

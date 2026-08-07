import zlib

from src.ec_pdf_decoder.objects import PDFDictionary, PDFName, PDFNumber, PDFStream
from src.ec_pdf_decoder.streams import (
    PDFStreamError,
    decode_stream,
    decode_stream_bytes,
    declared_length,
)


def dictionary(*items):
    return PDFDictionary(tuple(items))


def test_flate_decode():
    original = b"/CMapName /Adobe-Identity-UCS\nbeginbfrange\n"
    compressed = zlib.compress(original)
    stream = PDFStream(
        dictionary(
            (PDFName(b"Filter"), PDFName(b"FlateDecode")),
            (PDFName(b"Length"), PDFNumber(len(compressed))),
        ),
        compressed,
    )
    assert decode_stream(stream) == original


def test_abbreviated_flate_filter():
    original = b"hello bangla"
    compressed = zlib.compress(original)
    d = dictionary((PDFName(b"Filter"), PDFName(b"Fl")))
    assert decode_stream_bytes(d, compressed) == original


def test_filter_array_applies_in_order():
    # Two FlateDecode filters are unusual but valid as a filter pipeline.
    original = b"double compressed"
    compressed = zlib.compress(zlib.compress(original))
    d = dictionary(
        (
            PDFName(b"Filter"),
            __import__("src.ec_pdf_decoder.objects", fromlist=["PDFArray"]).PDFArray(
                (PDFName(b"FlateDecode"), PDFName(b"FlateDecode"))
            ),
        )
    )
    assert decode_stream_bytes(d, compressed) == original


def test_declared_length():
    d = dictionary((PDFName(b"Length"), PDFNumber(685)))
    assert declared_length(d) == 685


def test_unsupported_filter_fails():
    d = dictionary((PDFName(b"Filter"), PDFName(b"DCTDecode")))
    try:
        decode_stream_bytes(d, b"data")
    except PDFStreamError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("expected PDFStreamError")

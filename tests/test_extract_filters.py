from src.ec_pdf_decoder.extract import _decode_stream, _dict_value


def test_dict_value_accepts_compact_pdf_name_syntax():
    body = b"<</Filter/FlateDecode/Length 2777>>stream\nplaceholder\nendstream"
    assert _dict_value(body, b"Filter") == b"/FlateDecode"
    assert _dict_value(body, b"Length") == b"2777"


def test_decode_stream_accepts_compact_flate_dictionary():
    import zlib

    original = b"BT\n/F1 10 Tf\n<0038> Tj\nET\n"
    compressed = zlib.compress(original)
    body = b"<</Filter/FlateDecode/Length %d>>stream\n" % len(compressed)
    body += compressed + b"\nendstream"

    assert _decode_stream(body) == original

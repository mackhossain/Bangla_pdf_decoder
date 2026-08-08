from src.ec_pdf_decoder.extract import _decode_identity_h, parse_tounicode


def test_identity_h_decodes_two_byte_cids():
    assert _decode_identity_h(bytes.fromhex("002F004F0122")) == [47, 79, 290]


def test_tounicode_bfchar_supports_multiple_codepoints():
    cmap = b"""beginbfchar\n<0122> <09A8 09CD 09A4>\nendbfchar"""
    assert parse_tounicode(cmap)[0x122] == "ন্ত"

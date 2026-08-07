from src.ec_pdf_decoder.cmap import parse_cmap
from src.ec_pdf_decoder.content import TextShow
from src.ec_pdf_decoder.textdecode import decode_text, decode_text_show


CMAP = b'''\nbegincmap\n1 begincodespacerange\n<0000><FFFF>\nendcodespacerange\n3 beginbfchar\n<0003><0020>\n<001B><0981>\n<0038><09A3>\nendbfchar\nendcmap\n'''


def test_decode_two_byte_font_codes():
    cmap = parse_cmap(CMAP)
    assert cmap.decode_bytes(bytes.fromhex("0003001b0038")) == " \u0981\u09a3"


def test_decode_text_show():
    cmap = parse_cmap(CMAP)
    operation = TextShow(b"Tj", bytes.fromhex("001b0038"))
    assert decode_text_show(operation, cmap) == "\u0981\u09a3"


def test_decode_tj_ignores_position_adjustments():
    cmap = parse_cmap(CMAP)
    operation = TextShow(b"TJ", (bytes.fromhex("0003"), 120, bytes.fromhex("001b0038"), -30))
    assert decode_text_show(operation, cmap) == " \u0981\u09a3"


def test_decode_text_preserves_operation_order():
    cmap = parse_cmap(CMAP)
    operations = [
        TextShow(b"Tj", bytes.fromhex("001b")),
        TextShow(b"Tj", bytes.fromhex("0038")),
    ]
    assert decode_text(operations, cmap) == "\u0981\u09a3"


def test_unknown_code_becomes_replacement_character():
    cmap = parse_cmap(CMAP)
    assert cmap.decode_bytes(bytes.fromhex("9999")) == "\ufffd"

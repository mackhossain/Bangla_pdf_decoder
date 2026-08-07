from src.ec_pdf_decoder.content import (
    ContentSyntaxError,
    PDFHexString,
    PDFName,
    PDFNumber,
    PDFOperator,
    PDFString,
    TextShow,
    extract_text_show_operations,
    tokenize,
)


def test_literal_string_and_tj():
    ops = extract_text_show_operations(b"BT (hello) Tj ET")
    assert [(x.operator, x.value) for x in ops] == [(b"Tj", b"hello")]


def test_hex_string_and_tj():
    ops = extract_text_show_operations(b"BT <0003001B0038> Tj ET")
    assert ops[0].value == bytes.fromhex("0003001b0038")


def test_literal_escapes():
    ops = extract_text_show_operations(rb"BT (a\n\101\(b\)) Tj ET")
    assert ops[0].value == b"a\nA(b)"


def test_tj_array_preserves_strings_and_numbers():
    ops = extract_text_show_operations(b"BT [<0003> 120 (abc) -30] TJ ET")
    assert ops == [
        TextShow(b"TJ", (b"\x00\x03", 120, b"abc", -30))
    ]


def test_names_numbers_and_comments_tokenize():
    tokens = list(tokenize(b"/F1 12 Tf % comment\nBT"))
    assert tokens == [PDFName(b"F1"), PDFNumber(12), PDFOperator(b"Tf"), PDFOperator(b"BT")]


def test_unterminated_string_fails():
    try:
        list(tokenize(b"(broken"))
    except ContentSyntaxError as exc:
        assert "unterminated" in str(exc)
    else:
        raise AssertionError("expected ContentSyntaxError")


def test_tj_without_operand_fails():
    try:
        extract_text_show_operations(b"BT Tj ET")
    except ContentSyntaxError as exc:
        assert "operand" in str(exc)
    else:
        raise AssertionError("expected ContentSyntaxError")

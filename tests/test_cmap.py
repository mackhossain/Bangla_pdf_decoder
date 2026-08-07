from src.ec_pdf_decoder.cmap import CMapError, parse_cmap


CMAP = b'''% test CMap
/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange
<0000><FFFF>
endcodespacerange
2 beginbfchar
<0003><0020>
<001B><0981>
endbfchar
2 beginbfrange
<001E><0020><0985>
<0038><003A><09A1>
endbfrange
endcmap
CMapName currentdict /CMap defineresource pop
end
end
'''


def test_parse_cmap_codespace():
    cmap = parse_cmap(CMAP)
    assert len(cmap.codespaces) == 1
    assert cmap.codespaces[0].start == 0
    assert cmap.codespaces[0].end == 0xFFFF
    assert cmap.codespaces[0].width == 2


def test_parse_bfchar():
    cmap = parse_cmap(CMAP)
    assert cmap.lookup(0x0003) == (0x20,)
    assert cmap.lookup(0x001B) == (0x0981,)


def test_parse_bfrange():
    cmap = parse_cmap(CMAP)
    assert cmap.lookup(0x001E) == (0x0985,)
    assert cmap.lookup(0x001F) == (0x0986,)
    assert cmap.lookup(0x0020) == (0x0987,)
    assert cmap.lookup(0x0038) == (0x09A1,)
    assert cmap.lookup(0x0039) == (0x09A2,)
    assert cmap.lookup(0x003A) == (0x09A3,)


def test_decode_code():
    cmap = parse_cmap(CMAP)
    assert cmap.decode_code(0x001B) == "\u0981"
    assert cmap.decode_code(0x0039) == "\u09a2"
    assert cmap.decode_code(0x9999) == "\ufffd"


def test_missing_codespace_is_rejected():
    try:
        parse_cmap(b"1 beginbfchar <0001><0041> endbfchar")
    except CMapError as exc:
        assert "codespace" in str(exc)
    else:
        raise AssertionError("expected CMapError")

from src.ec_pdf_decoder.direct_pdf import classify_cids, decode_operation
from src.ec_pdf_decoder.content import TextShow


def test_manual_mapping_decodes_but_remains_native_unresolved():
    native = {56: "গ"}
    effective = {56: "গ", 207: "ে", 290: "ন্ত"}
    text, cids, missing_for_effective_decode = decode_operation(
        TextShow(b"Tj", bytes.fromhex("003800CF0122")), effective
    )

    assert text == "গেন্ত"
    assert cids == [56, 207, 290]
    assert missing_for_effective_decode == []
    unresolved, fallback_decoded, unknown = classify_cids(set(cids), native, effective)
    assert unresolved == [207, 290]
    assert fallback_decoded == [207, 290]
    assert unknown == []


def test_completely_unknown_cid_is_separate_from_fallback_decoded_cid():
    unresolved, fallback_decoded, unknown = classify_cids(
        {207, 290, 999}, {}, {207: "ে", 290: "ন্ত"}
    )
    assert unresolved == [207, 290, 999]
    assert fallback_decoded == [207, 290]
    assert unknown == [999]

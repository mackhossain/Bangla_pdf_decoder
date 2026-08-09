from src.ec_pdf_decoder.bangla_order import restore_bangla_logical_order_tokens


def decode(tokens: list[str]) -> str:
    return "".join(restore_bangla_logical_order_tokens(tokens))


def test_reph_moves_before_cluster():
    assert decode(["ও", "য়", "া", "ড", "র্"]) == "ওয়ার্ড"


def test_prebase_i_moves_after_conjunct():
    assert decode(["দ", "ি", "ক্ষ", "ণ"]) == "দক্ষিণ"


def test_prebase_i_moves_after_simple_base():
    assert decode(["ি", "স", "ি", "ট"]) == "সিটি"


def test_reph_and_two_part_vowel():
    assert decode(["ক", "ে", "প", "র্", "া", "ে", "র", "শ", "ন"]) == "কর্পোরেশন"


def test_prebase_e_and_two_part_vowel():
    assert decode(["ে", "ক", "া", "ত", "য়", "া", "ল", "ী"]) == "কোতয়ালী"


def test_ordinary_logical_bengali_is_not_reordered_without_visual_signal():
    assert decode(["ক", "ে", "ন"]) == "কেন"


def test_whole_custom_glyph_is_not_split_for_reph():
    # CID 387 maps to the complete custom glyph "দুর্".  It must not be
    # mistaken for the standalone CID 206 reph glyph "র্".
    assert decode(["দুর্"]) == "দুর্"

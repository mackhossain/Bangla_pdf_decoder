from src.ec_pdf_decoder.bangla_order import restore_bangla_logical_order_tokens


def decode(tokens: list[str]) -> str:
    return "".join(restore_bangla_logical_order_tokens(tokens, visual_order=True))


def test_real_pdf_examples():
    assert decode(["অ", "ি", "ম", "ত", "া"]) == "অমিতা"
    assert decode(["গৃ", "ি", "হ", "ন", "ী"]) == "গৃহিনী"
    assert decode(["ত", "া", "ি", "র", "খ"]) == "তারিখ"


def test_existing_ec_examples():
    assert decode(["ও", "য়", "া", "ড", "র্"]) == "ওয়ার্ড"
    assert decode(["দ", "ি", "ক্ষ", "ণ"]) == "দক্ষিণ"
    assert decode(["ি", "স", "ি", "ট"]) == "সিটি"
    assert decode(["ক", "ে", "প", "র্", "া", "ে", "র", "শ", "ন"]) == "কর্পোরেশন"
    assert decode(["ে", "ক", "া", "ত", "য়", "া", "ল", "ী"]) == "কোতয়ালী"


def test_custom_conjunct_stays_atomic():
    assert decode(["দুর্"]) == "দুর্"

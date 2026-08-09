from pathlib import Path

from src.ec_pdf_decoder.bangla_cleanup import cleanup_bangla_text


def test_confirmed_page3_repairs():
    text = "দুর্র্গা ঘোষ\nসম্পা রানী েঘাষ\nশঁাখারী বাজার\nসুবণর্া নাগ\nঅন্নপূর্না সেন\nসমর্ত কর"
    assert cleanup_bangla_text(text) == (
        "দুর্গা ঘোষ\nসম্পা রানী ঘোষ\nশাখারী বাজার\nসুবর্ণা নাগ\nঅন্নপূর্ণা সেন\nসমর্থ কর"
    )


def test_leading_visual_vowel_only_at_run_start():
    assert cleanup_bangla_text("িপতা: লক্ষী") == "পিতা: লক্ষী"
    assert cleanup_bangla_text("েঘাষ") == "ঘোষ"
    assert cleanup_bangla_text("উজ্জলা িবশ্বাস") == "উজ্জলা বিশ্বাস"


def test_normal_logical_bangla_is_not_reordered():
    assert cleanup_bangla_text("সেন, কোতয়ালী, ঢাকা") == "সেন, কোতয়ালী, ঢাকা"
    assert cleanup_bangla_text("কর্মচারী") == "কর্মচারী"


def test_external_correction_file_is_supported(tmp_path: Path):
    corrections = tmp_path / "corrections.json"
    corrections.write_text('{"ভুলশব্দ": "সঠিকশব্দ"}', encoding="utf-8")
    assert cleanup_bangla_text("ভুলশব্দ", corrections) == "সঠিকশব্দ"

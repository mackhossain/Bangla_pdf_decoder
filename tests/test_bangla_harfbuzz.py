from src.ec_pdf_decoder.bangla_harfbuzz import choose_candidate, gid_sequence_score


def test_gid_sequence_score_is_order_sensitive():
    assert gid_sequence_score([1, 2, 3], [1, 2, 3]) == 1.0
    assert gid_sequence_score([1, 2, 3], [3, 2, 1]) < 1.0


def test_choose_candidate_falls_back_to_reordered_without_font():
    text, score = choose_candidate(None, [1, 2], "অিম", "অমি")
    assert text == "অমি"
    assert score is None

from nepali_corpus.core.utils.normalize import normalize_text, make_dedup_key, detect_nepali


def test_normalize_text_collapses_whitespace():
    text = "  यो   एउटा\nपरीक्षण   हो  "
    assert normalize_text(text) == "यो एउटा परीक्षण हो"


def test_dedup_key_stable_for_equivalent_text():
    a = "नेपाल सरकार"
    b = "नेपाल   सरकार!!"
    assert make_dedup_key(a) == make_dedup_key(b)


def test_detect_nepali_ratio():
    nepali = "नेपाल सरकारको सूचना"
    english = "Government notice"
    assert detect_nepali(nepali, min_ratio=0.4)
    assert not detect_nepali(english, min_ratio=0.4)

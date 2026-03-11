from nepali_corpus.core.models import RawRecord
from nepali_corpus.core.utils.normalize import normalize_record
from nepali_corpus.core.utils.cleaning import is_nepali, min_length


def test_normalize_and_filter_helpers():
    rec = RawRecord(
        source_id="x",
        source_name="X",
        url="http://x",
        title="नेपाल सरकारको सूचना",
        language="ne",
    )
    doc = normalize_record(rec)
    assert doc is not None
    assert is_nepali(doc, min_ratio=0.4)
    assert not min_length(doc, min_chars=200)

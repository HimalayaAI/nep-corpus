from nepali_corpus.core.models import NormalizedDocument
from nepali_corpus.core.utils.dedup import deduplicate


def test_deduplicate_by_url_and_key():
    d1 = NormalizedDocument(
        id="1",
        url="http://x",
        text="नेपाल सरकार",
        language="ne",
        source_id="a",
        source_name="A",
        dedup_key="k1",
    )
    d2 = NormalizedDocument(
        id="2",
        url="http://x",
        text="नेपाल सरकार",
        language="ne",
        source_id="b",
        source_name="B",
        dedup_key="k1",
    )
    d3 = NormalizedDocument(
        id="3",
        url="http://y",
        text="नेपाल सरकार",
        language="ne",
        source_id="c",
        source_name="C",
        dedup_key="k1",
    )

    unique = deduplicate([d1, d2, d3])
    assert len(unique) == 1
    assert unique[0].url == "http://x"

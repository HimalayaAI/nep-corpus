from nepali_corpus.core.services.dashboard.sources import get_sources


def test_get_sources_returns_catalog():
    sources = get_sources(refresh=True)
    assert isinstance(sources, list)
    assert sources
    sample = sources[0]
    assert "id" in sample
    assert "name" in sample
    assert "category" in sample

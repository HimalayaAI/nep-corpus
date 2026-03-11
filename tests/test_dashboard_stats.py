import asyncio

from nepali_corpus.core.services.dashboard.stats import fetch_stats


class FakeSession:
    async def get_stats(self):
        return {
            "total_documents": 3,
            "by_source": {"rss": 2, "govt": 1},
            "by_language": {"ne": 2, "en": 1},
        }


def test_fetch_stats_uses_session():
    session = FakeSession()
    stats = asyncio.run(fetch_stats(session))
    assert stats["total_documents"] == 3
    assert stats["by_source"]["rss"] == 2
    assert stats["by_language"]["en"] == 1

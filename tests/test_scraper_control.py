import asyncio

from nepali_corpus.core.models import RawRecord
from nepali_corpus.core.services.scraper_control import ScrapeCoordinator


class FakeSession:
    async def seen_url(self, url: str) -> bool:
        return False

    async def mark_url(self, url: str) -> None:
        return None


class FakeStorage:
    def create_session(self):
        return FakeSession()


def test_scrape_coordinator_runs(monkeypatch, tmp_path):
    record = RawRecord(
        source_id="govt_test",
        source_name="Govt Test",
        url="http://example.com",
        title="Title",
        language="ne",
    )

    monkeypatch.setattr(
        "nepali_corpus.core.services.scrapers.govt_scraper.fetch_raw_records",
        lambda *args, **kwargs: [record],
    )
    monkeypatch.setattr(
        "nepali_corpus.core.services.scrapers.dao_scraper.fetch_raw_records",
        lambda *args, **kwargs: [],
    )

    coordinator = ScrapeCoordinator(FakeStorage())
    asyncio.run(
        coordinator._run(
            workers=1,
            max_pages=1,
            categories=["Gov"],
            pdf_enabled=False,
            gzip_output=False,
            output_path=str(tmp_path / "raw.jsonl"),
            pdf_output_dir=str(tmp_path / "pdfs"),
            govt_registry_path=None,
            govt_registry_groups=None,
        )
    )

    assert coordinator.state.docs_saved == 1
    assert coordinator.state.urls_crawled == 1

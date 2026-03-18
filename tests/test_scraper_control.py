"""Tests for scraper coordinator (updated for run tracking refactor)."""
import asyncio

from nepali_corpus.core.models import RawRecord
from nepali_corpus.core.services.scrapers.control import ScrapeCoordinator, ScrapeJob


class FakeSession:
    """Minimal mock — supports URL tracking + run tracking no-ops."""
    def __init__(self):
        self._urls = set()

    async def seen_url(self, url: str) -> bool:
        return url in self._urls

    async def mark_url(self, url: str) -> None:
        self._urls.add(url)

    async def store_raw_records(self, records) -> int:
        count = 0
        for r in records:
            self._urls.add(r.url)
            count += 1
        return count

    async def store_training_documents(self, docs) -> int:
        return len(list(docs))

    async def count_urls(self) -> int:
        return len(self._urls)

    # Run tracking no-ops
    async def create_pipeline_run(self, run_id, sources=None, categories=None, config=None, output_dir=None):
        return 0

    async def update_pipeline_run(self, run_id, **kwargs):
        pass

    async def create_pipeline_job(self, pipeline_run_id, job_type, source_id,
                                   source_name=None, category=None, scraper_class=None):
        return 0

    async def update_pipeline_job(self, job_id, **kwargs):
        pass

    async def get_pending_jobs(self, run_id, job_type=None):
        return []

    async def get_run_status(self, run_id):
        return None

    async def list_runs(self, limit=20):
        return []


class FakeStorage:
    def create_session(self):
        return FakeSession()


def test_scrape_coordinator_runs(monkeypatch, tmp_path):
    record = RawRecord(
        source_id="test_source",
        source_name="Test Source",
        url="http://example.com/page1",
        title="Test Title",
        language="ne",
    )

    # Patch _build_jobs to avoid network and registry
    def _mock_build_jobs(self, categories, max_pages, govt_registry_path, govt_registry_groups, num_sources=None):
        return [
            ScrapeJob(
                name="mock_source",
                category="Gov",
                scraper_class="generic",
                func=lambda: [record],
            ),
        ]

    monkeypatch.setattr(ScrapeCoordinator, "_build_jobs", _mock_build_jobs)

    # Patch enrich_records
    def _mock_enrich_records(records, *args, **kwargs):
        # enrich_records returns [(record, content_string), ...]
        return [(rec, rec.content or "mock_content_so_it_has_length_over_100" * 5) for rec in records]
        
    monkeypatch.setattr("nepali_corpus.core.services.scrapers.control.enrich_records", _mock_enrich_records)

    # Use enrichment_batch_size of 1000 so the buffer won't auto-flush during the test
    coordinator = ScrapeCoordinator(FakeStorage(), enrichment_batch_size=1000)

    async def _run():
        await coordinator.start(
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
        # Wait for coordinator with a timeout
        for _ in range(100):
            if not coordinator.is_running():
                break
            await asyncio.sleep(0.1)

    asyncio.run(_run())

    assert coordinator.state.docs_saved >= 1
    assert coordinator.state.urls_crawled >= 1

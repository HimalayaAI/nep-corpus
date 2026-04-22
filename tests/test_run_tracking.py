"""Tests for pipeline run tracking (no DB, no network required)."""
import asyncio
import json

import pytest

from nepali_corpus.core.models import RawRecord
from nepali_corpus.core.services.scrapers.control import ScrapeCoordinator, ScrapeJob


# ---------------------------------------------------------------------------
# Fake storage implementation (in-memory, for tests)
# ---------------------------------------------------------------------------

class FakeSession:
    """In-memory mock of StorageSession with run tracking."""

    def __init__(self):
        self._seen_urls = set()
        self._runs = {}          # run_id -> dict
        self._jobs = {}          # job_id -> dict
        self._next_run_id = 1
        self._next_job_id = 1

    async def seen_url(self, url: str) -> bool:
        return url in self._seen_urls

    async def mark_url(self, url: str) -> None:
        self._seen_urls.add(url)

    async def count_urls(self) -> int:
        return len(self._seen_urls)

    async def store_raw_records(self, records) -> int:
        count = 0
        for r in records:
            self._seen_urls.add(r.url)
            count += 1
        return count

    async def store_training_documents(self, docs) -> int:
        return len(list(docs))

    async def create_pipeline_run(self, run_id, sources=None, categories=None, config=None, output_dir=None):
        db_id = self._next_run_id
        self._next_run_id += 1
        self._runs[run_id] = {
            "id": db_id,
            "run_id": run_id,
            "sources_requested": sources or [],
            "categories": categories or [],
            "status": "running",
            "total_jobs": 0,
            "completed_jobs": 0,
            "failed_jobs": 0,
            "total_records_scraped": 0,
            "total_records_saved": 0,
            "output_dir": output_dir,
        }
        return db_id

    async def update_pipeline_run(self, run_id, **kwargs):
        if run_id in self._runs:
            self._runs[run_id].update(kwargs)

    async def create_pipeline_job(self, pipeline_run_id, job_type, source_id,
                                   source_name=None, category=None, scraper_class=None):
        db_id = self._next_job_id
        self._next_job_id += 1
        self._jobs[db_id] = {
            "id": db_id,
            "pipeline_run_id": pipeline_run_id,
            "job_type": job_type,
            "source_id": source_id,
            "source_name": source_name,
            "category": category,
            "scraper_class": scraper_class,
            "status": "pending",
            "records_crawled": 0,
            "records_saved": 0,
            "records_failed": 0,
            "attempt_number": 1,
            "max_attempts": 3,
        }
        return db_id

    async def update_pipeline_job(self, job_id, **kwargs):
        if job_id in self._jobs:
            self._jobs[job_id].update(kwargs)

    async def get_pending_jobs(self, run_id, job_type=None):
        run = self._runs.get(run_id)
        if not run:
            return []
        rid = run["id"]
        result = []
        for j in self._jobs.values():
            if j["pipeline_run_id"] != rid:
                continue
            if j["status"] not in ("pending", "interrupted"):
                continue
            if j["attempt_number"] > j["max_attempts"]:
                continue
            if job_type and j["job_type"] != job_type:
                continue
            result.append(dict(j))
        return result

    async def get_run_status(self, run_id):
        return self._runs.get(run_id)

    async def list_runs(self, limit=20):
        return list(self._runs.values())[:limit]


class FakeStorage:
    def __init__(self):
        self._session = FakeSession()

    def create_session(self):
        return self._session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_coordinator_creates_run_and_jobs(monkeypatch, tmp_path):
    """Coordinator should create a pipeline_run and per-source jobs."""
    record = RawRecord(
        source_id="dao_test",
        source_name="DAO Test",
        url="http://example.com/test1",
        title="Test Title",
        language="ne",
    )

    # Patch _build_jobs to return deterministic jobs that don't hit network
    def _mock_build_jobs(self, categories, max_pages, govt_registry_path, govt_registry_groups, num_sources=None):
        return [
            ScrapeJob(
                name="mock_dao",
                category="Gov",
                scraper_class="dao",
                func=lambda: [record],
            ),
            ScrapeJob(
                name="mock_empty",
                category="Gov",
                scraper_class="generic",
                func=lambda: [],
            ),
        ]

    monkeypatch.setattr(ScrapeCoordinator, "_build_jobs", _mock_build_jobs)

    storage = FakeStorage()
    coordinator = ScrapeCoordinator(storage)

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
            run_id="test_run_001",
        )
        # Wait for task to finish
        while coordinator.is_running():
            await asyncio.sleep(0.1)

    asyncio.run(_run())

    session = storage._session

    # Pipeline run should exist
    assert "test_run_001" in session._runs
    run = session._runs["test_run_001"]
    assert run["status"] == "completed"

    # Jobs should have been created (2 scrape jobs + 1 enrichment pipeline job)
    assert len(session._jobs) == 3

    # All jobs should be completed
    completed = [j for j in session._jobs.values() if j["status"] == "completed"]
    assert len(completed) == 3  # two scrape jobs + one enrichment job


def test_coordinator_tracks_failed_jobs(monkeypatch, tmp_path):
    """Jobs that raise should be marked as failed in DB."""

    def _mock_build_jobs(self, categories, max_pages, govt_registry_path, govt_registry_groups, num_sources=None):
        def _failing():
            raise RuntimeError("scraper boom")
        return [
            ScrapeJob(
                name="failing_src",
                category="Gov",
                scraper_class="generic",
                func=_failing,
            ),
        ]

    monkeypatch.setattr(ScrapeCoordinator, "_build_jobs", _mock_build_jobs)

    storage = FakeStorage()
    coordinator = ScrapeCoordinator(storage)

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
            run_id="test_fail_001",
        )
        while coordinator.is_running():
            await asyncio.sleep(0.1)

    asyncio.run(_run())

    session = storage._session
    failed = [j for j in session._jobs.values() if j["status"] == "failed"]
    assert len(failed) == 1
    assert "scraper boom" in failed[0].get("error_message", "")


def test_coordinator_shutdown_marks_interrupted(monkeypatch, tmp_path):
    """Graceful shutdown should mark remaining jobs as interrupted."""
    import time

    def _mock_build_jobs(self, categories, max_pages, govt_registry_path, govt_registry_groups, num_sources=None):
        def _slow():
            time.sleep(5)  # simulate slow scrape
            return []
        return [
            ScrapeJob(
                name="slow_src",
                category="Gov",
                scraper_class="generic",
                func=_slow,
            ),
        ]

    monkeypatch.setattr(ScrapeCoordinator, "_build_jobs", _mock_build_jobs)

    storage = FakeStorage()
    coordinator = ScrapeCoordinator(storage)

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
            run_id="test_shutdown_001",
        )
        # Request shutdown immediately
        await asyncio.sleep(0.5)
        coordinator.request_shutdown()
        while coordinator.is_running():
            await asyncio.sleep(0.1)

    asyncio.run(_run())

    session = storage._session
    run = session._runs.get("test_shutdown_001")
    assert run is not None
    assert run["status"] == "interrupted"


def test_coordinator_checkpoint(tmp_path):
    """write_checkpoint should produce a valid JSON file."""
    storage = FakeStorage()
    coordinator = ScrapeCoordinator(storage)
    coordinator._run_id = "ckpt_001"
    coordinator.state.urls_crawled = 42
    coordinator.state.docs_saved = 10
    coordinator.state.urls_failed = 2

    output_dir = str(tmp_path / "run_ckpt")
    coordinator.write_checkpoint(output_dir)

    ckpt_path = tmp_path / "run_ckpt" / "checkpoint.json"
    assert ckpt_path.exists()

    with open(ckpt_path) as f:
        data = json.load(f)

    assert data["run_id"] == "ckpt_001"
    assert data["urls_crawled"] == 42
    assert data["docs_saved"] == 10
    assert data["status"] == "completed"


def test_fake_session_pending_jobs():
    """get_pending_jobs should return only pending/interrupted jobs."""
    session = FakeSession()

    async def _test():
        rid = await session.create_pipeline_run("run_pend")
        j1 = await session.create_pipeline_job(rid, "scrape", "src_a")
        j2 = await session.create_pipeline_job(rid, "scrape", "src_b")
        j3 = await session.create_pipeline_job(rid, "scrape", "src_c")

        # Mark j1 as completed, j2 stays pending, j3 is interrupted
        await session.update_pipeline_job(j1, status="completed")
        await session.update_pipeline_job(j3, status="interrupted")

        pending = await session.get_pending_jobs("run_pend", job_type="scrape")
        pending_ids = {p["source_id"] for p in pending}

        assert "src_a" not in pending_ids  # completed
        assert "src_b" in pending_ids       # pending
        assert "src_c" in pending_ids       # interrupted

    asyncio.run(_test())


def test_scraper_base_retry():
    """ScraperBase.fetch_page should retry on 5xx."""
    from nepali_corpus.core.services.scrapers.scraper_base import ScraperBase
    from unittest.mock import MagicMock

    scraper = ScraperBase(base_url="http://example.com", delay=0)

    call_count = 0

    def mock_get(url, timeout=30):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        if call_count < 3:
            resp.status_code = 503
        else:
            resp.status_code = 200
            resp.text = "<html><body>OK</body></html>"
        return resp

    scraper.session.get = mock_get
    result = scraper.fetch_page("http://example.com/test", timeout=5)

    assert result is not None
    assert call_count == 3  # Two 503s then success


def test_scraper_base_no_retry_on_404():
    """ScraperBase.fetch_page should NOT retry on 404."""
    from nepali_corpus.core.services.scrapers.scraper_base import ScraperBase
    from unittest.mock import MagicMock

    scraper = ScraperBase(base_url="http://example.com", delay=0)
    call_count = 0

    def mock_get(url, timeout=30):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status_code = 404
        return resp

    scraper.session.get = mock_get
    result = scraper.fetch_page("http://example.com/missing", timeout=5)

    assert result is None
    assert call_count == 1  # No retries


def test_storage_session_abstract_defaults():
    """StorageSession base class run tracking methods should return no-op defaults."""
    from nepali_corpus.core.services.storage.storage import StorageSession

    # Can't instantiate ABC directly, but we can test the mixin methods
    # by creating a minimal concrete subclass
    class MinimalSession(StorageSession):
        async def store_training_document(self, doc):
            return ""
        async def store_training_documents(self, docs):
            return 0
        async def list_recent_documents(self, limit=50):
            return []
        async def get_stats(self):
            return {}
        async def seen_url(self, url):
            return False
        async def mark_url(self, url):
            pass
        async def count_urls(self):
            return 0

    session = MinimalSession()

    async def _test():
        # These should all return defaults without error
        run_id = await session.create_pipeline_run("r1")
        assert run_id == 0

        await session.update_pipeline_run("r1", status="done")

        job_id = await session.create_pipeline_job(0, "scrape", "src")
        assert job_id == 0

        await session.update_pipeline_job(0, status="done")

        pending = await session.get_pending_jobs("r1")
        assert pending == []

        status = await session.get_run_status("r1")
        assert status is None

        runs = await session.list_runs()
        assert runs == []

    asyncio.run(_test())

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set
from urllib.parse import urlparse

from pydantic import ConfigDict

from nepali_corpus.core.models import RawRecord, TrainingDocument
from nepali_corpus.core.models.base import BaseEntity
from nepali_corpus.core.services.scrapers.pdf import PdfJob, extract_pdfs, HAS_PYMUPDF
from nepali_corpus.core.services.scrapers import (
    dao_scraper,
    ekantipur_scraper,
    govt_scraper,
    news_rss_scraper,
    regulatory_scraper,
    miner,
)
from nepali_corpus.core.services.scrapers.registry import load_registry
from nepali_corpus.core.services.storage.env_storage import EnvStorageService
from nepali_corpus.core.utils import JsonlWriter
from nepali_corpus.core.utils.boilerplate import BoilerplateDetector
from nepali_corpus.core.utils.content_types import identify_content_type
from nepali_corpus.core.utils.rate_limiter import DomainRateLimiter
from nepali_corpus.core.utils.url_set import UrlSet
from nepali_corpus.pipeline.runner import enrich_records, to_training_docs

logger = logging.getLogger("nepali_corpus.scrapers.control")


class ScrapeJob(BaseEntity):
    """A runnable scrape job dispatched by the coordinator."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str
    category: str
    func: Callable[..., Any] # Can be generator or direct function
    scraper_class: str = "generic"
    is_discovery: bool = False


class ScrapeState:
    def __init__(self) -> None:
        self.running = False
        self.paused = False
        self.urls_crawled = 0
        self.urls_failed = 0
        self.docs_saved = 0
        self.pdf_saved = 0
        self.jobs_completed = 0
        self.start_time: Optional[float] = None
        self.current_sources: List[str] = []
        self.errors: List[str] = []
        self.source_stats: Dict[str, Dict[str, int]] = {}

    def reset(self) -> None:
        self.running = False
        self.paused = False
        self.urls_crawled = 0
        self.urls_failed = 0
        self.docs_saved = 0
        self.pdf_saved = 0
        self.jobs_completed = 0
        self.start_time = None
        self.current_sources = []
        self.errors = []
        self.source_stats = {}

    def record_source(self, source_id: str, crawled: int = 0, saved: int = 0, failed: int = 0) -> None:
        stats = self.source_stats.setdefault(source_id, {"crawled": 0, "saved": 0, "failed": 0})
        stats["crawled"] += crawled
        stats["saved"] += saved
        stats["failed"] += failed

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        if len(self.errors) > 200:
            self.errors.pop(0)

    def speed_urls_per_min(self) -> float:
        if not self.start_time:
            return 0.0
        elapsed = (time.time() - self.start_time) / 60
        return round(self.urls_crawled / elapsed, 1) if elapsed > 0 else 0.0

    def elapsed_str(self) -> str:
        if not self.start_time:
            return "00:00:00"
        sec = int(time.time() - self.start_time)
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "paused": self.paused,
            "urls_crawled": self.urls_crawled,
            "urls_failed": self.urls_failed,
            "docs_saved": self.docs_saved,
            "pdf_saved": self.pdf_saved,
            "speed": self.speed_urls_per_min(),
            "elapsed": self.elapsed_str(),
            "current_sources": self.current_sources[-5:],
            "recent_errors": self.errors[-10:],
            "source_stats": self.source_stats,
        }


class ScrapeCoordinator:
    def __init__(
        self,
        storage: EnvStorageService,
        enrichment_batch_size: int = 50,
        rate_limit: float = 2.0,
        max_concurrent: int = 20,
        source_timeout: int = 300,
        checkpoint_interval: int = 300,
        ocr_enabled: bool = False,
        pdf_enabled: bool = False,
        enrichment_workers: int = 10,
        skip_successful_only: bool = True,
    ) -> None:
        self._storage = storage
        self.state = ScrapeState()
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self._internet_down = False
        self._enrichment_workers = enrichment_workers
        self._skip_successful_only = skip_successful_only

        # Run tracking state
        self._run_id: Optional[str] = None
        self._db_run_id: Optional[int] = None
        self._job_db_ids: Dict[str, int] = {}  # job.name -> DB pipeline_jobs.id
        self._log_handler: Optional[logging.Handler] = None

        # Production features
        self._enrichment_batch_size = enrichment_batch_size
        self._enrichment_buffer: List[RawRecord] = []
        self._enrichment_buffer_last_flush = time.time()
        self._enrichment_buffer_max_age = 60.0
        self._enrichment_lock = asyncio.Lock()
        self._ocr_enabled = ocr_enabled
        self._pdf_enabled = pdf_enabled
        self._source_timeout = source_timeout
        self._checkpoint_interval = checkpoint_interval
        self._visited_urls = UrlSet()
        self._boilerplate_detector = BoilerplateDetector()
        self._rate_limiter = DomainRateLimiter(
            default_rate=rate_limit,
            max_concurrent=max_concurrent,
        )
        self._checkpoint_task: Optional[asyncio.Task] = None
        self._enrichment_flush_task: Optional[asyncio.Task] = None
        self._enrichment_tasks: Set[asyncio.Task] = set()
        self._enrichment_task_sem = asyncio.Semaphore(2)
        self._cache_dir: str = "data/html_cache"
        self._discovery_futures: List = []
        self._writer: Optional[JsonlWriter] = None

    def is_running(self) -> bool:
        return self.state.running

    def request_shutdown(self) -> None:
        """Signal graceful shutdown (from signal handler)."""
        self._shutdown_event.set()
        self._stop_event.set()

    async def _load_visited_urls(self, session: Any) -> None:
        """Preload visited URLs from DB into memory for fast O(1) dedup."""
        try:
            if self._skip_successful_only:
                logger.info("Retrying failures mode: Only skipping URLs found in training_documents...")
                # Fetch URL hashes from training_documents instead of visited_urls
                if hasattr(session, 'service') and session.service._db:
                    rows = await session.service._db.fetch(
                        "SELECT url FROM training_documents"
                    )
                    self._visited_urls.add_many(row[0] for row in rows)
                    logger.info("Loaded %d successful URLs to skip", len(self._visited_urls))
                return

            count = await session.count_urls()
            if count > 0:
                logger.info("Loading %d visited URLs into memory...", count)
                # Fetch URL hashes from DB
                if hasattr(session, 'service') and session.service._db:
                    rows = await session.service._db.fetch(
                        "SELECT url FROM visited_urls"
                    )
                    self._visited_urls.add_many(row[0] for row in rows)
                    logger.info("Loaded %d visited URLs", len(self._visited_urls))
        except Exception as e:
            logger.warning("Could not preload visited URLs: %s", e)

    def _schedule_enrichment(self, session: Any, records: List[RawRecord]) -> None:
        """Schedule enrichment in tracked background tasks with bounded concurrency."""
        if not records:
            return

        async def _runner() -> None:
            async with self._enrichment_task_sem:
                await self._process_immediate_enrichment(session, records)

        task = asyncio.create_task(_runner())
        self._enrichment_tasks.add(task)
        task.add_done_callback(self._enrichment_tasks.discard)

    async def _drain_enrichment_tasks(self) -> None:
        """Wait for all scheduled enrichment tasks to finish."""
        if not self._enrichment_tasks:
            return

        pending = list(self._enrichment_tasks)
        logger.info("Waiting for %d enrichment tasks to finish", len(pending))
        results = await asyncio.gather(*pending, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                logger.error("Background enrichment task failed: %s", res)

    async def _maybe_flush_enrichment(self, session: Any, force: bool = False) -> None:
        """Flush enrichment buffer when threshold or time limit reached."""
        async with self._enrichment_lock:
            if not self._enrichment_buffer:
                return
            
            buffer_age = time.time() - self._enrichment_buffer_last_flush
            should_flush = (
                force or
                len(self._enrichment_buffer) >= self._enrichment_batch_size or
                (len(self._enrichment_buffer) > 0 and buffer_age > self._enrichment_buffer_max_age)
            )
            
            if not should_flush:
                return

            batch = list(self._enrichment_buffer)
            self._enrichment_buffer.clear()
            self._enrichment_buffer_last_flush = time.time()

        if batch:
            logger.info(
                "Flushing enrichment batch of %d records (age=%.1fs)",
                len(batch),
                buffer_age if force else time.time() - self._enrichment_buffer_last_flush
            )
            self._schedule_enrichment(session, batch)

    async def _periodic_checkpoint(self, output_dir: str) -> None:
        """Background task to write checkpoints at regular intervals."""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self._checkpoint_interval)
                if self.state.running:
                    self.write_checkpoint(output_dir)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Checkpoint write failed: %s", e)
    
    async def _periodic_enrichment_flush(self, session: Any) -> None:
        """Background task to periodically flush enrichment buffer (time-based).
        
        Prevents buffer from growing unbounded during long-running scrapes.
        Flushes every 30 seconds if buffer is non-empty.
        """
        flush_interval = 30  # seconds
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(flush_interval)
                if self.state.running:
                    await self._maybe_flush_enrichment(session, force=False)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Periodic enrichment flush failed: %s", e)

    async def start(
        self,
        *,
        workers: int = 4,
        max_pages: Optional[int] = None,
        categories: Optional[List[str]] = None,
        pdf_enabled: bool = False,
        ocr_enabled: bool = False,
        gzip_output: bool = False,
        output_path: str = "data/raw/raw.jsonl",
        pdf_output_dir: str = "data/pdfs",
        govt_registry_path: Optional[str] = None,
        govt_registry_groups: Optional[List[str]] = None,
        run_id: Optional[str] = None,
        output_dir: Optional[str] = None,
        log_file: Optional[str] = None,
        num_sources: Optional[int] = None,
    ) -> None:
        if self._task and not self._task.done():
            raise RuntimeError("Scraper already running")
        self._stop_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self.state.reset()
        self.state.running = True
        self.state.start_time = time.time()

        self._run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")

        async def _run_wrapper():
            try:
                await self._run(
                    workers=workers,
                    max_pages=max_pages,
                    categories=categories,
                    pdf_enabled=pdf_enabled,
                    ocr_enabled=ocr_enabled,
                    gzip_output=gzip_output,
                    output_path=output_path,
                    pdf_output_dir=pdf_output_dir,
                    govt_registry_path=govt_registry_path,
                    govt_registry_groups=govt_registry_groups,
                    output_dir=output_dir,
                    log_file=log_file,
                    num_sources=num_sources,
                )
            except Exception as e:
                logger.exception(f"Coordinator _run task crashed with an unhandled exception: {e}")
            finally:
                self.state.running = False
                self._stop_event.set()
                self._shutdown_event.set()

        self._task = asyncio.create_task(_run_wrapper())

    async def stop(self) -> None:
        self._stop_event.set()
        self.state.running = False
        self.state.paused = False
        if self._task:
            await asyncio.sleep(0)

    def pause(self) -> None:
        self.state.paused = True

    def resume_paused(self) -> None:
        self.state.paused = False

    async def resume_run(
        self,
        run_id: str,
        *,
        workers: int = 4,
        max_pages: Optional[int] = None,
        pdf_enabled: bool = False,
        gzip_output: bool = False,
        output_path: str = "data/raw/raw.jsonl",
        pdf_output_dir: str = "data/pdfs",
        govt_registry_path: Optional[str] = None,
        govt_registry_groups: Optional[List[str]] = None,
        output_dir: Optional[str] = None,
        log_file: Optional[str] = None,
        num_sources: Optional[int] = None,
    ) -> None:
        """Resume an interrupted run by re-dispatching pending jobs."""
        if self._task and not self._task.done():
            raise RuntimeError("Scraper already running")
        self._stop_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self.state.reset()
        self.state.running = True
        self.state.start_time = time.time()
        self._run_id = run_id

        async def _resume_wrapper():
            try:
                await self._resume(
                    run_id=run_id,
                    workers=workers,
                    max_pages=max_pages,
                    pdf_enabled=pdf_enabled,
                    gzip_output=gzip_output,
                    output_path=output_path,
                    pdf_output_dir=pdf_output_dir,
                    govt_registry_path=govt_registry_path,
                    govt_registry_groups=govt_registry_groups,
                    output_dir=output_dir,
                    log_file=log_file,
                    num_sources=num_sources,
                )
            except Exception as e:
                logger.exception(f"Coordinator _resume task crashed with an unhandled exception: {e}")
            finally:
                self.state.running = False
                self._stop_event.set()
                self._shutdown_event.set()

        self._task = asyncio.create_task(_resume_wrapper())

    async def run_rerun_failed(
        self,
        *,
        batch_size: int = 100,
        limit: Optional[int] = None,
        output_dir: Optional[str] = None,
        log_file: Optional[str] = None,
    ) -> None:
        """Rerun extraction for existing raw_records with NULL content."""
        if self._task and not self._task.done():
            raise RuntimeError("Scraper already running")

        self._stop_event = asyncio.Event()
        self._shutdown_event = asyncio.Event()
        self.state.reset()
        self.state.running = True
        self.state.start_time = time.time()
        self._run_id = f"rerun_failed_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        async def _rerun_wrapper():
            try:
                # Initialize logging
                if log_file:
                    self._setup_logging(log_file)

                session = self._storage.create_session()
                await self._load_visited_urls(session)

                # Query NULL records that are NOT in training_documents
                query = """
                    SELECT r.* FROM raw_records r
                    LEFT JOIN training_documents t ON r.url = t.url
                    WHERE r.content IS NULL AND t.url IS NULL
                """
                query_args = []
                if limit:
                    query += " LIMIT $1"
                    query_args.append(limit)

                async with self._storage._db.pool.acquire() as conn:
                    rows = await conn.fetch(query, *query_args)
                    records = []
                    for row in rows:
                        data = dict(row)
                        # Handle JSON fields that might be strings
                        if isinstance(data.get("raw_meta"), str):
                            try:
                                data["raw_meta"] = json.loads(data["raw_meta"])
                            except Exception:
                                data["raw_meta"] = {}
                        if isinstance(data.get("tags"), str):
                            try:
                                data["tags"] = json.loads(data["tags"])
                            except Exception:
                                data["tags"] = []

                        rec = RawRecord(**data)
                        records.append(rec)

                    logger.info("Found %d records needing rerun (NULL content)", len(records))
                    if not records:
                        logger.info("Nothing to rerun.")
                        return

                    # Process in batches
                    for i in range(0, len(records), batch_size):
                        if self._shutdown_event.is_set():
                            break
                        
                        batch = records[i : i + batch_size]
                        logger.info("Rerunning batch %d/%d...", (i // batch_size) + 1, (len(records) // batch_size + 1))
                        await self._process_immediate_enrichment(session, batch)
                        
                        # Update stats
                        self.state.urls_crawled += len(batch)
                        
                        # Periodic logging
                        if i % (batch_size * 5) == 0:
                            logger.info("Progress: %d/%d rerun complete", i, len(records))

                    logger.info("Rerun-failed run completed.")
            except Exception as e:
                logger.exception(f"Rerun-failed failed: {e}")
            finally:
                self.state.running = False
                if self._log_handler:
                    logger.removeHandler(self._log_handler)
                    self._log_handler.close()

        self._task = asyncio.create_task(_rerun_wrapper())

    def _setup_logging(self, log_file: str) -> None:
        """Helper to redirect logs to a run-specific file."""
        import logging
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handler = logging.FileHandler(log_file)
        handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(handler)
        self._log_handler = handler

    def _build_jobs(
        self,
        categories: Optional[List[str]],
        max_pages: Optional[int],
        govt_registry_path: Optional[str],
        govt_registry_groups: Optional[List[str]],
        num_sources: Optional[int] = None,
    ) -> List[ScrapeJob]:
        """Build scrape job list from the unified SourceRegistry."""
        from nepali_corpus.core.services.scrapers.source_registry import SourceRegistry
        
        categories_lower = {c.lower() for c in (categories or ["Gov", "News"])}
        
        # Load unified registry
        registry_dir = os.path.dirname(govt_registry_path) if govt_registry_path else "sources"
        reg = SourceRegistry(registry_dir)
        reg.load_all()
        
        jobs: List[ScrapeJob] = []
        
        # --- Gov Category ---
        if "gov" in categories_lower or "government" in categories_lower:
            gov_sources = reg.list(source_type="government")
            if govt_registry_groups:
                allowed = set(g.strip() for g in govt_registry_groups)
                gov_sources = [s for s in gov_sources if s.category in allowed]
                
            for cfg in gov_sources:
                if cfg.scraper_class == "ministry_generic":
                    from nepali_corpus.core.services.scrapers.govt_scraper import MinistryScraper, MinistryConfig, post_to_raw
                    # translate SourceConfig to MinistryConfig for the scraper
                    mcfg = MinistryConfig(
                        source_id=cfg.id,
                        name=cfg.name,
                        name_ne=cfg.name_ne,
                        base_url=cfg.url,
                        endpoints=cfg.endpoints or {},
                        priority=cfg.effective_priority,
                    )
                    jobs.append(
                        ScrapeJob(
                            name=f"gov:{cfg.id}",
                            category="Gov",
                            scraper_class="ministry_generic",
                            func=lambda c=mcfg: [
                                post_to_raw(p) for posts in MinistryScraper(c).scrape_all(max_pages_per_endpoint=max_pages or 3).values() for p in posts
                            ]
                        )
                    )
                elif cfg.scraper_class == "regulatory" or cfg.scraper_class == "nrb_scraper":
                    from nepali_corpus.core.services.scrapers.regulatory_scraper import RegulatoryScraper
                    from nepali_corpus.core.models.government_schemas import RegistryEntry
                    # translate to RegistryEntry
                    rentry = RegistryEntry(
                        source_id=cfg.id, name=cfg.name, name_ne=cfg.name_ne,
                        base_url=cfg.url, endpoints=cfg.endpoints or {},
                        scraper_class="regulatory", priority=cfg.effective_priority
                    )
                    jobs.append(
                        ScrapeJob(
                            name=f"reg:{cfg.id}",
                            category="Gov",
                            scraper_class="regulatory",
                            func=lambda e=rentry: RegulatoryScraper(e).scrape(pages=max_pages or 1)
                        )
                    )
                else:
                    # Fallback for constitutional, parliament, etc.
                    from nepali_corpus.core.services.scrapers.regulatory_scraper import RegulatoryScraper
                    from nepali_corpus.core.models.government_schemas import RegistryEntry
                    rentry = RegistryEntry(
                        source_id=cfg.id, name=cfg.name, name_ne=cfg.name_ne,
                        base_url=cfg.url, endpoints=cfg.endpoints or {},
                        scraper_class=cfg.scraper_class or "generic", priority=cfg.effective_priority
                    )
                    jobs.append(
                        ScrapeJob(
                            name=f"gov:{cfg.id}",
                            category="Gov",
                            scraper_class=cfg.scraper_class or "generic",
                            func=lambda e=rentry: RegulatoryScraper(e).scrape(pages=max_pages or 1)
                        )
                    )
            
            # --- Discovery for Gov Sources ---
            for cfg in gov_sources:
                if cfg.is_discovery:
                    # Discovery Miner Job
                    miner_obj = miner.DiscoveryMiner(cfg.url)
                    jobs.append(
                        ScrapeJob(
                            name=f"discovery:{cfg.id}",
                            category="Discovery",
                            scraper_class="miner",
                            is_discovery=True,
                            func=lambda m=miner_obj: m.discover_all(max_pages=max_pages or 500)
                        )
                    )

            if not govt_registry_groups:
                jobs.append(
                    ScrapeJob(
                        name="dao", category="Gov", scraper_class="dao",
                        func=lambda: dao_scraper.fetch_raw_records(pages=max_pages or 50)
                    )
                )

        # --- News Category ---
        if "news" in categories_lower:
            # 1. Existing RSS aggregate job
            jobs.append(
                ScrapeJob(
                    name="news_rss", category="News", scraper_class="rss",
                    func=lambda: news_rss_scraper.fetch_raw_records()
                )
            )
            # 2. Existing Ekantipur specialized job
            jobs.append(
                ScrapeJob(
                    name="ekantipur", category="News", scraper_class="ekantipur",
                    func=lambda: ekantipur_scraper.fetch_raw_records(max_pages=max_pages or 500)
                )
            )
            # 3. Generic HTML sources (thousands)
            html_news = reg.list(source_type="html")
            for cfg in html_news:
                if getattr(cfg, "is_discovery", False):
                    jobs.append(
                        ScrapeJob(
                            name=f"miner_news:{cfg.id}",
                            category="News",
                            scraper_class="miner",
                            is_discovery=True,
                            func=lambda c=cfg: miner.DiscoveryMiner(c.url).discover_all(max_pages=max_pages or 100)
                        )
                    )
                else:
                    rec = RawRecord(
                        source_id=cfg.id,
                        source_name=cfg.name,
                        url=cfg.url,
                        title=cfg.name,
                        language=cfg.language or "ne",
                    )
                    jobs.append(
                        ScrapeJob(
                            name=f"news_html:{cfg.id}",
                            category="News",
                            scraper_class="generic_html",
                            func=lambda r=rec: [r]
                        )
                    )

        # --- Social Category ---
        if "social" in categories_lower:
            from .social_scraper import NitterScraper
            social_sources = reg.list(source_type="social")
            scraper = NitterScraper(["https://nitter.poast.org", "https://nitter.privacydev.net"])

            for cfg in social_sources:
                cat = cfg.category
                if cat == "hashtag" and "meta" in cfg and "tag" in cfg.meta:
                    jobs.append(
                        ScrapeJob(
                            name=f"social_search:hashtag:{cfg.meta['tag']}",
                            category="Social",
                            scraper_class="nitter",
                            func=lambda q=f"#{cfg.meta['tag']}": scraper.fetch_search_tweets(q, max_pages=max_pages or 1),
                        )
                    )
                elif cat == "search" and "meta" in cfg and "query" in cfg.meta:
                    jobs.append(
                        ScrapeJob(
                            name=f"social_search:{cfg.meta['query']}",
                            category="Social",
                            scraper_class="nitter",
                            func=lambda q=cfg.meta['query']: scraper.fetch_search_tweets(q, max_pages=max_pages or 1),
                        )
                    )
                elif "meta" in cfg and "username" in cfg.meta:
                    jobs.append(
                        ScrapeJob(
                            name=f"social:{cfg.meta['username']}",
                            category="Social",
                            scraper_class="nitter",
                            func=lambda u=cfg.meta['username']: scraper.fetch_user_tweets(u, max_pages=max_pages or 1),
                        )
                    )

        if num_sources:
            return jobs[:num_sources]
            
        return jobs

    async def _run(
        self,
        *,
        workers: int,
        max_pages: Optional[int],
        categories: Optional[List[str]],
        pdf_enabled: bool,
        ocr_enabled: bool = True,
        gzip_output: bool,
        output_path: str,
        pdf_output_dir: str,
        govt_registry_path: Optional[str],
        govt_registry_groups: Optional[List[str]],
        output_dir: Optional[str],
        log_file: Optional[str] = None,
        num_sources: Optional[int] = None,
    ) -> None:
        self._ocr_enabled = bool(ocr_enabled)
        self._pdf_enabled = bool(pdf_enabled)
        if log_file:
            self._setup_file_logging(log_file)

        session = self._storage.create_session()

        # Preload visited URLs for fast in-memory dedup
        await self._load_visited_urls(session)

        # Set cache dir relative to the run's output directory
        if output_dir:
            self._cache_dir = os.path.join(output_dir, "html_cache")
        elif output_path:
            self._cache_dir = os.path.join(os.path.dirname(output_path), "html_cache")
        else:
            self._cache_dir = "data/html_cache"

        jobs = self._build_jobs(categories, max_pages, govt_registry_path, govt_registry_groups, num_sources=num_sources)

        # Sort by priority — high-value sources first
        jobs.sort(key=lambda j: getattr(j, '_priority', 3))

        if not jobs:
            self.state.add_error("No matching scrapers for selected categories")
            self.state.running = False
            return

        logger.info(
            "Starting run %s with %d jobs, %d workers, %d visited URLs preloaded",
            self._run_id, len(jobs), workers, len(self._visited_urls),
        )

        config = {
            "workers": workers,
            "max_pages": max_pages,
            "categories": categories,
            "pdf_enabled": pdf_enabled,
            "gzip_output": gzip_output,
            "num_sources": num_sources,
            "enrichment_batch_size": self._enrichment_batch_size,
            "source_timeout": self._source_timeout,
        }
        try:
            self._db_run_id = await session.create_pipeline_run(
                run_id=self._run_id,
                sources=[j.name for j in jobs],
                categories=categories,
                config=config,
                output_dir=output_dir or os.path.dirname(output_path),
            )
            await session.update_pipeline_run(self._run_id, total_jobs=len(jobs))
        except Exception as e:
            logger.warning("Failed to create pipeline run record: %s", e)
            self._db_run_id = 0

        for job in jobs:
            try:
                db_id = await session.create_pipeline_job(
                    pipeline_run_id=self._db_run_id,
                    job_type="scrape",
                    source_id=job.name,
                    source_name=job.name,
                    category=job.category,
                    scraper_class=job.scraper_class,
                )
                self._job_db_ids[job.name] = db_id
            except Exception as e:
                logger.warning("Failed to create job record for %s: %s", job.name, e)

        if output_dir:
            self._checkpoint_task = asyncio.create_task(
                self._periodic_checkpoint(output_dir)
            )
        
        # Start periodic enrichment buffer flush (prevents memory leak from unbounded buffering)
        self._enrichment_flush_task = asyncio.create_task(
            self._periodic_enrichment_flush(session)
        )

        writer = JsonlWriter(output_path, gzip_output=gzip_output, append=True)
        self._writer = writer
        try:
            await self._execute_jobs(
                jobs=jobs,
                session=session,
                writer=writer,
                workers=workers,
                pdf_enabled=pdf_enabled,
                pdf_output_dir=pdf_output_dir,
            )
            await asyncio.sleep(0)
            for fut in self._discovery_futures:
                try:
                    await asyncio.wrap_future(fut)
                except Exception as e:
                    logger.warning("Discovery batch coroutine failed: %s", e)
            self._discovery_futures.clear()
        finally:
            writer.flush()

        await self._maybe_flush_enrichment(session, force=True)
        await self._drain_enrichment_tasks()
        writer.close()
        self._writer = None

        if self.state.urls_crawled > 0:
            await self._run_enrichment(session, output_path, gzip_output, workers)

        if self._checkpoint_task:
            self._checkpoint_task.cancel()
            try:
                await self._checkpoint_task
            except asyncio.CancelledError:
                pass
        
        if self._enrichment_flush_task:
            self._enrichment_flush_task.cancel()
            try:
                await self._enrichment_flush_task
            except asyncio.CancelledError:
                pass

        await self._finalize_run(session)
        self._cleanup_file_logging()
        self._log_run_summary()

    async def _resume(
        self,
        run_id: str,
        *,
        workers: int,
        max_pages: Optional[int],
        pdf_enabled: bool,
        gzip_output: bool,
        output_path: str,
        pdf_output_dir: str,
        govt_registry_path: Optional[str],
        govt_registry_groups: Optional[List[str]],
        output_dir: Optional[str],
        log_file: Optional[str] = None,
        num_sources: Optional[int] = None,
    ) -> None:
        """Resume an interrupted run."""
        if log_file:
            self._setup_file_logging(log_file)

        session = self._storage.create_session()

        # Get pending jobs from DB
        pending = await session.get_pending_jobs(run_id, job_type="scrape")
        if not pending:
            logger.info("No pending jobs to resume for run %s", run_id)
            self.state.running = False
            return

        # Get the run's DB id
        run_status = await session.get_run_status(run_id)
        if run_status:
            self._db_run_id = run_status["id"]

        await session.update_pipeline_run(run_id, status="running")

        # Build a name -> func map from all possible jobs (respecting num_sources filters if provided)
        all_jobs = self._build_jobs(None, max_pages, govt_registry_path, govt_registry_groups, num_sources=num_sources)
        func_map = {j.name: j.func for j in all_jobs}

        # Rebuild job list from pending DB records
        jobs: List[ScrapeJob] = []
        for pj in pending:
            source_id = pj["source_id"]
            func = func_map.get(source_id)
            if func is None:
                logger.warning("No scraper func found for source %s, skipping", source_id)
                continue
            self._job_db_ids[source_id] = pj["id"]
            # Increment attempt number
            await session.update_pipeline_job(pj["id"], attempt_number=pj["attempt_number"] + 1)
            jobs.append(
                ScrapeJob(
                    name=source_id,
                    category=pj.get("category", "News"),
                    scraper_class=pj.get("scraper_class", "generic_html"),
                    func=func,
                )
            )

        if not jobs:
            logger.info("No resumable jobs found for run %s", run_id)
            self.state.running = False
            return

        # Start periodic enrichment buffer flush (prevents memory leak from unbounded buffering)
        self._enrichment_flush_task = asyncio.create_task(
            self._periodic_enrichment_flush(session)
        )

        writer = JsonlWriter(output_path, gzip_output=gzip_output, append=True)
        try:
            await self._execute_jobs(
                jobs=jobs,
                session=session,
                writer=writer,
                workers=workers,
                pdf_enabled=pdf_enabled,
                pdf_output_dir=pdf_output_dir,
            )
        finally:
            writer.flush()
            writer.close()

        # Match the normal run path so buffered records are not stranded on resume.
        await self._maybe_flush_enrichment(session, force=True)
        await self._drain_enrichment_tasks()

        # --- Enrichment Phase (for any items that need it) ---
        if not self._stop_event.is_set():
            await self._run_enrichment(session, output_path, gzip_output, workers)

        if self._enrichment_flush_task:
            self._enrichment_flush_task.cancel()
            try:
                await self._enrichment_flush_task
            except asyncio.CancelledError:
                pass

        await self._finalize_run(session)
        self._cleanup_file_logging()


    async def _execute_jobs(
        self,
        *,
        jobs: List[ScrapeJob],
        session: Any,
        writer: JsonlWriter,
        workers: int,
        pdf_enabled: bool,
        pdf_output_dir: str,
    ) -> None:
        """Execute scrape jobs in a thread pool and write results."""
        loop = asyncio.get_running_loop()
        pending: List[asyncio.Future] = []
        pdf_jobs: List[PdfJob] = []
        discovery_futures = self._discovery_futures
        discovery_futures_lock = threading.Lock()

        async def _process_batch_records(j, records):
            """Helper to bridge threadpool batches back to main loop."""
            if self._stop_event.is_set():
                return
            await self._handle_results(j, records, session, writer, pdf_enabled, pdf_output_dir, pdf_jobs)

        def _run_job(j: ScrapeJob):
            try:
                if j.is_discovery:
                    # Discovery jobs yield batches of URLs
                    for batch_urls in j.func():
                        if not batch_urls:
                            continue
                        
                        # Convert to RawRecord objects
                        records = [
                            RawRecord(
                                source_id=j.name.split(":")[-1],
                                source_name=j.name,
                                url=url,
                                title="",
                                language="ne"
                            ) for url in batch_urls
                        ]
                        # Dispatch to main loop for handling
                        fut = asyncio.run_coroutine_threadsafe(
                            _process_batch_records(j, records),
                            loop
                        )
                        with discovery_futures_lock:
                            discovery_futures.append(fut)
                    return j, [] # No final records for discovery job
                else:
                    # Standard jobs return List[RawRecord]
                    return j, j.func()
            except Exception as e:
                return j, e

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for job in jobs:
                # Mark job as running in DB
                db_id = self._job_db_ids.get(job.name)
                if db_id:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            session.update_pipeline_job(
                                db_id,
                                status="running",
                                started_at=datetime.now(timezone.utc),
                            ),
                            loop
                        )
                    except Exception:
                        pass

                future = loop.run_in_executor(pool, _run_job, job)
                pending.append(future)

            for coro in asyncio.as_completed(pending):
                if self._stop_event.is_set():
                    break
                
                # Global Pause/Internet check
                while (self.state.paused or self._internet_down) and not self._stop_event.is_set():
                    if self._internet_down:
                        if await self._check_internet_restored():
                            self._internet_down = False
                        else:
                            await asyncio.sleep(10)
                    else:
                        await asyncio.sleep(0.5)

                job_start = time.time()
                try:
                    job, records = await coro
                    if isinstance(records, Exception):
                        raise records
                    
                    if records:
                        await self._handle_results(job, records, session, writer, pdf_enabled, pdf_output_dir, pdf_jobs)
                except Exception as exc:
                    job_name = "unknown"
                    try: job_name = job.name
                    except: pass

                    # Internet loss check
                    if self._is_connection_error(exc) and not await self._probe_internet():
                        logger.warning("Detected global internet loss. Pausing coordinator.")
                        self._internet_down = True
                    
                    msg = f"{job_name} failed: {exc}"
                    logger.error(msg)
                    self.state.urls_failed += 1
                    self.state.add_error(msg)

                    db_id = self._job_db_ids.get(job_name)
                    if db_id:
                        await session.update_pipeline_job(
                            db_id,
                            status="failed",
                            error_message=str(exc)[:500],
                            completed_at=datetime.now(timezone.utc),
                        )
                    continue

                # Finalize Success status for Job in DB
                db_id = self._job_db_ids.get(job.name)
                if db_id:
                    try:
                        await session.update_pipeline_job(
                            db_id,
                            status="completed",
                            completed_at=datetime.now(timezone.utc),
                            duration_ms=(time.time() - job_start) * 1000,
                        )
                    except Exception:
                        pass
                self.state.jobs_completed += 1

        # Handle queued PDFs after all scrapers are done
        if pdf_enabled and pdf_jobs and HAS_PYMUPDF:
            try:
                logger.info("Starting PDF extraction for %d queued files", len(pdf_jobs))
                pdf_records = await extract_pdfs(
                    pdf_jobs,
                    output_dir=pdf_output_dir,
                    max_workers=max(2, workers),
                    seen_url=session.seen_url,
                    mark_url=session.mark_url,
                )
                for rec in pdf_records:
                    writer.write(rec)
                    self.state.record_source(rec.source_id, crawled=1, saved=1)
                    await session.store_raw_records([rec])
                    # Stream PDF to enrichment as well
                    self._schedule_enrichment(session, [rec])

                self.state.pdf_saved += len(pdf_records)
                self.state.urls_crawled += len(pdf_records)
            except Exception as exc:
                self.state.add_error(f"PDF extraction failed: {exc}")

        # If shutdown was requested, mark in-flight jobs as interrupted
        if self._shutdown_event.is_set():
            for job in jobs:
                db_id = self._job_db_ids.get(job.name)
                if db_id:
                    try:
                        # Only update jobs that are still 'running'
                        await session.update_pipeline_job(db_id, status="interrupted")
                    except Exception:
                        pass

    async def _handle_results(
        self,
        job: ScrapeJob,
        records: List[RawRecord],
        session: Any,
        writer: JsonlWriter,
        pdf_enabled: bool,
        pdf_output_dir: str,
        pdf_jobs: List[PdfJob],
    ) -> None:
        if not records:
            return

        t0 = time.perf_counter()

        urls = [r.url for r in records]
        seen_set = set()
        
        
        if not self._skip_successful_only:
            try:
                seen_set = await session.seen_urls_batch(urls)
            except AttributeError:
                for u in urls:
                    if await session.seen_url(u):
                        seen_set.add(u)

        new_records = []
        new_urls = []
        for record in records:
            self.state.record_source(record.source_id, crawled=1)
            if self._visited_urls.contains(record.url) or record.url in seen_set:
                continue

            if pdf_enabled and record.url.lower().endswith(".pdf"):
                pdf_jobs.append(PdfJob(
                    url=record.url,
                    source_id=record.source_id,
                    source_name=record.source_name,
                    category=record.category,
                ))
                continue

            new_records.append(record)
            new_urls.append(record.url)

        if new_records:
            stored_ok = False
            try:
                await session.store_raw_records(new_records)
                stored_ok = True
            except Exception as exc:
                logger.error("store_raw_records failed (continuing): %s", exc)

            if stored_ok:
                try:
                    await session.mark_urls_batch(new_urls)
                except AttributeError:
                    for u in new_urls:
                        await session.mark_url(u)
                for url in new_urls:
                    self._visited_urls.add(url)

            for record in new_records:
                self.state.record_source(record.source_id, saved=1)

        self.state.urls_crawled += len(records)
        self.state.docs_saved += len(new_records)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        rps = len(records) / max((time.perf_counter() - t0), 0.001)
        logger.debug(
            "handle_results: %d records in, %d new, %.1fms, %.0f rec/s",
            len(records), len(new_records), elapsed_ms, rps,
        )

        if new_records:
            async with self._enrichment_lock:
                if not self._enrichment_buffer:
                    self._enrichment_buffer_last_flush = time.time()
                self._enrichment_buffer.extend(new_records)
            await self._maybe_flush_enrichment(session)

    async def _process_immediate_enrichment(self, session: Any, records: List[RawRecord]) -> None:
        """Run enrichment and training doc sync out-of-band."""

        try:
            from nepali_corpus.pipeline.runner import enrich_records
            enriched = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: enrich_records(
                    records, 
                    cache_dir=self._cache_dir,
                    ocr_enabled=self._ocr_enabled,
                    pdf_enabled=self._pdf_enabled,
                    max_workers=10
                )
            )
        except Exception as e:
            logger.error("Streaming enrichment fetch failed for batch of %d: %s", len(records), e)
            if self._writer:
                for rec in records:
                    self._writer.write(rec)
            return

        final_docs = []
        enriched_count = 0
        for rec, content in enriched:
            try:
                if content:
                    rec.content = content
                    enriched_count += 1
                    cleaned = self._boilerplate_detector.clean_document(content, rec.source_id)
                    
                    if len(cleaned.strip()) >= 200:
                        from nepali_corpus.core.utils.normalize import devanagari_ratio
                        ratio = devanagari_ratio(cleaned)
                        lang = "ne" if ratio >= 0.4 else "en"
                        
                        doc_id = hashlib.md5(rec.url.encode()).hexdigest()
                        doc = TrainingDocument(
                            id=doc_id,
                            url=rec.url,
                            source_id=rec.source_id,
                            source_name=rec.source_name,
                            language=lang,
                            text=cleaned.strip(),
                            category=rec.category,
                            published_at=rec.published_at,
                            date_bs=rec.raw_meta.get("date_bs") if rec.raw_meta else None,
                        )
                        final_docs.append(doc)
                        if lang == "en":
                            logger.debug("Tagged English article: %s (ratio %.2f)", rec.url, ratio)
            except Exception as e:
                logger.warning("Enrichment processing failed for %s: %s", rec.url, e)
                continue

        if self._writer:
            for rec, _ in enriched:
                self._writer.write(rec)
            self._writer.flush()

        logger.info(
            "Streaming enrichment: %d/%d fetched content, %d training docs created",
            enriched_count, len(records), len(final_docs),
        )

        if final_docs:
            try:
                await session.store_training_documents(final_docs)
            except Exception as e:
                logger.error("Failed to store %d training documents: %s", len(final_docs), e)
        
        enriched_recs = [rec for rec, content in enriched if content]
        if enriched_recs:
            try:
                await session.store_raw_records(enriched_recs)
            except Exception as e:
                logger.error("Failed to update raw records after enrichment: %s", e)


    async def _run_enrichment(
        self,
        session: Any,
        output_path: str,
        gzip_output: bool,
        workers: int,
    ) -> None:
        """Run enrichment on the records produced by this run.

        Uses BoilerplateDetector for cross-document boilerplate removal
        instead of inline logic.
        """
        from nepali_corpus.pipeline.runner import load_raw_jsonl, save_raw_jsonl
        from nepali_corpus.core.utils.normalize import devanagari_ratio

        # Create enrichment job record
        job_id = await session.create_pipeline_job(
            pipeline_run_id=self._db_run_id,
            job_type="enrich",
            source_id="run_enrichment",
            source_name="Full Text Enrichment",
            category="Pipeline",
            scraper_class="enricher",
        )

        await session.update_pipeline_job(
            job_id,
            status="running",
            started_at=datetime.now(timezone.utc),
        )

        start_time = time.time()
        try:
            # Load the current raw records from the output file
            records = load_raw_jsonl(output_path)
            if not records:
                await session.update_pipeline_job(job_id, status="completed", records_saved=0)
                return

            unenriched = [r for r in records if not r.content]
            already_enriched = [r for r in records if r.content]
            logger.info(
                "Post-run enrichment: %d total records, %d already enriched, %d need enrichment",
                len(records), len(already_enriched), len(unenriched),
            )

            enriched_records = list(already_enriched)  # Keep already-enriched as-is

            if unenriched:
                enriched_pairs = enrich_records(
                    unenriched,
                    cache_dir=self._cache_dir,
                    ocr_enabled=self._ocr_enabled,
                    pdf_enabled=self._pdf_enabled,
                )
                for rec, content in enriched_pairs:
                    if content:
                        rec.content = content
                    enriched_records.append(rec)

            import shutil as _shutil
            _tmp_path = output_path + ".enriching.tmp"
            save_raw_jsonl(enriched_records, _tmp_path, gzip_output=gzip_output)
            _shutil.move(_tmp_path, output_path)

            # --- Cross-document boilerplate removal via BoilerplateDetector ---
            # Group texts by domain for profile learning
            texts_with_domains = []
            for rec in enriched_records:
                text = (rec.content or "").strip()
                if not text:
                    continue
                domain = urlparse(rec.url).netloc.lower() if rec.url else rec.source_id
                texts_with_domains.append((text, domain, rec))

            # Feed all texts to the boilerplate detector
            domain_texts: Dict[str, List[str]] = {}
            for text, domain, _ in texts_with_domains:
                domain_texts.setdefault(domain, []).append(text)
            for domain, txts in domain_texts.items():
                self._boilerplate_detector.update_profile(txts, domain)

            # Clean and filter
            training_docs = []
            for text, domain, rec in texts_with_domains:
                cleaned = self._boilerplate_detector.clean_document(text, domain)
                if len(cleaned) < 200:
                    continue
                
                ratio = devanagari_ratio(cleaned)
                lang = "ne" if ratio >= 0.4 else "en"
                
                doc_id = hashlib.md5(rec.url.encode()).hexdigest()
                doc = TrainingDocument(
                    id=doc_id,
                    url=rec.url,
                    source_id=rec.source_id,
                    source_name=rec.source_name,
                    language=lang,
                    text=cleaned,
                    published_at=rec.published_at,
                    date_bs=rec.raw_meta.get("date_bs") if rec.raw_meta else None,
                    category=rec.category,
                    content_type=rec.content_type,
                    tags=[],
                )
                training_docs.append(doc)
                if lang == "en":
                    logger.debug("Tagged English article in post-run enrichment: %s", rec.url)

            if training_docs:
                await session.store_training_documents(training_docs)
                self.state.docs_saved += len(training_docs)
                logger.info(
                    "Saved %s clean training documents (boilerplate stripped, %d domains profiled)",
                    len(training_docs),
                    self._boilerplate_detector.domain_count,
                )
            else:
                logger.info("No additional training documents passed cleaning filters")

            # Update raw_records table with enriched content
            await session.store_raw_records(enriched_records)

            await session.update_pipeline_job(
                job_id,
                status="completed",
                records_saved=len(enriched_records),
                completed_at=datetime.now(timezone.utc),
                duration_ms=(time.time() - start_time) * 1000,
            )
            logger.info("Enrichment complete for %s records", len(enriched_records))

        except Exception as e:
            logger.error("Enrichment failed: %s", e)
            await session.update_pipeline_job(
                job_id,
                status="failed",
                error_message=str(e),
                completed_at=datetime.now(timezone.utc),
            )

    async def _finalize_run(self, session: Any) -> None:
        """Update pipeline run with final counts."""
        if not self._run_id:
            self.state.running = False
            return

        final_status = "interrupted" if self._shutdown_event.is_set() else "completed"

        try:
            await session.update_pipeline_run(
                self._run_id,
                status=final_status,
                completed_jobs=self.state.jobs_completed,
                total_records_scraped=self.state.urls_crawled,
                total_records_saved=self.state.docs_saved,
                completed_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.warning("Failed to finalize pipeline run: %s", e)

        self.state.running = False

    def write_checkpoint(self, output_dir: str) -> None:
        """Write a checkpoint.json to the run directory."""
        checkpoint = {
            "run_id": self._run_id,
            "status": "interrupted" if self._shutdown_event.is_set() else "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "urls_crawled": self.state.urls_crawled,
            "docs_saved": self.state.docs_saved,
            "urls_failed": self.state.urls_failed,
            "source_stats": self.state.source_stats,
            "errors": self.state.errors[-20:],
        }
        path = os.path.join(output_dir, "checkpoint.json")
        os.makedirs(output_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, indent=2, ensure_ascii=False)
        logger.info("Checkpoint written to %s", path)

    def _setup_file_logging(self, log_file: str) -> None:
        """Set up logging to a file."""
        try:
            # Ensure directory exists
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)

            # Add file handler to the root logger to capture everything
            root_logger = logging.getLogger()
            handler = logging.FileHandler(log_file, encoding="utf-8", mode='a')
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
            )
            root_logger.addHandler(handler)
            root_logger.setLevel(logging.INFO)
            self._log_handler = handler
            logger.info("Logging to file: %s", log_file)
        except Exception as e:
            logger.warning("Failed to set up file logging to %s: %s", log_file, e)

    def _cleanup_file_logging(self) -> None:
        """Remove file handler from root logger."""
        if self._log_handler:
            try:
                root_logger = logging.getLogger()
                root_logger.removeHandler(self._log_handler)
                self._log_handler.close()
                self._log_handler = None
            except Exception:
                pass

    def _is_connection_error(self, exc: Exception) -> bool:
        """Check if exception looks like a network connection error."""
        import requests
        return isinstance(exc, (requests.ConnectionError, requests.Timeout))

    async def _probe_internet(self) -> bool:
        """Probe internet via DNS lookup — avoids HTTP which 8.8.8.8 does not serve."""
        import socket
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: socket.getaddrinfo("dns.google", 80, proto=socket.IPPROTO_TCP),
            )
            return True
        except Exception:
            return False


    async def _check_internet_restored(self) -> bool:
        """Check if internet is back after an outage."""
        return await self._probe_internet()

    def _log_run_summary(self) -> None:
        """Log a structured summary of the completed run."""
        state = self.state
        bp_stats = self._boilerplate_detector.stats()
        rl_stats = self._rate_limiter.stats()

        sources_attempted = len(state.source_stats)
        sources_with_saves = sum(
            1 for s in state.source_stats.values() if s.get("saved", 0) > 0
        )
        sources_failed = sum(
            1 for s in state.source_stats.values()
            if s.get("failed", 0) > 0 and s.get("saved", 0) == 0
        )
        total_crawled = state.urls_crawled
        total_saved = state.docs_saved
        success_rate = (
            f"{(total_saved / total_crawled * 100):.1f}%"
            if total_crawled > 0
            else "N/A"
        )

        logger.info(
            "\n" + "=" * 70 + "\n"
            "RUN SUMMARY: %s\n"
            "  Sources attempted : %d\n"
            "  Sources with data : %d\n"
            "  Sources failed    : %d\n"
            "  URLs crawled      : %d\n"
            "  Docs saved        : %d\n"
            "  PDFs saved        : %d\n"
            "  Success rate      : %s\n"
            "  Elapsed           : %s\n"
            "  Speed             : %.1f URLs/min\n"
            "  Domains profiled  : %d (boilerplate)\n"
            "  Domains throttled : %d (rate limiter)\n"
            "  Domains tripped   : %d (circuit breaker)\n"
            + "=" * 70,
            self._run_id,
            sources_attempted,
            sources_with_saves,
            sources_failed,
            total_crawled,
            total_saved,
            state.pdf_saved,
            success_rate,
            state.elapsed_str(),
            state.speed_urls_per_min(),
            bp_stats.get("domains_profiled", 0),
            rl_stats.get("domains_throttled", 0),
            rl_stats.get("domains_tripped", 0),
        )


__all__ = ["ScrapeCoordinator", "ScrapeState"]

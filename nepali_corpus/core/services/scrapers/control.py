from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Iterable, List, Optional

from pydantic import ConfigDict

from nepali_corpus.core.models import RawRecord
from nepali_corpus.core.models.base import BaseEntity
from nepali_corpus.core.services.scrapers.pdf import PdfJob, extract_pdfs, HAS_PYMUPDF
from nepali_corpus.core.services.scrapers import (
    dao_scraper,
    ekantipur_scraper,
    govt_scraper,
    news_rss_scraper,
    regulatory_scraper,
)
from nepali_corpus.core.services.scrapers.registry import load_registry
from nepali_corpus.core.services.storage.env_storage import EnvStorageService
from nepali_corpus.core.utils import JsonlWriter

logger = logging.getLogger("nepali_corpus.scrapers.control")


class ScrapeJob(BaseEntity):
    """A runnable scrape job dispatched by the coordinator.

    Inherits from:
        BaseEntity – common model config
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    category: str
    func: Callable[[], List[RawRecord]]


class ScrapeState:
    def __init__(self) -> None:
        self.running = False
        self.paused = False
        self.urls_crawled = 0
        self.urls_failed = 0
        self.docs_saved = 0
        self.pdf_saved = 0
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
    def __init__(self, storage: EnvStorageService) -> None:
        self._storage = storage
        self.state = ScrapeState()
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def is_running(self) -> bool:
        return self.state.running

    async def start(
        self,
        *,
        workers: int = 4,
        max_pages: Optional[int] = None,
        categories: Optional[List[str]] = None,
        pdf_enabled: bool = False,
        gzip_output: bool = False,
        output_path: str = "data/raw/raw.jsonl",
        pdf_output_dir: str = "data/pdfs",
        govt_registry_path: Optional[str] = None,
        govt_registry_groups: Optional[List[str]] = None,
    ) -> None:
        if self._task and not self._task.done():
            raise RuntimeError("Scraper already running")
        self._stop_event = asyncio.Event()
        self.state.reset()
        self.state.running = True
        self.state.start_time = time.time()
        self._task = asyncio.create_task(
            self._run(
                workers=workers,
                max_pages=max_pages,
                categories=categories,
                pdf_enabled=pdf_enabled,
                gzip_output=gzip_output,
                output_path=output_path,
                pdf_output_dir=pdf_output_dir,
                govt_registry_path=govt_registry_path,
                govt_registry_groups=govt_registry_groups,
            )
        )

    async def stop(self) -> None:
        self._stop_event.set()
        self.state.running = False
        self.state.paused = False
        if self._task:
            await asyncio.sleep(0)

    def pause(self) -> None:
        self.state.paused = True

    def resume(self) -> None:
        self.state.paused = False

    async def _run(
        self,
        *,
        workers: int,
        max_pages: Optional[int],
        categories: Optional[List[str]],
        pdf_enabled: bool,
        gzip_output: bool,
        output_path: str,
        pdf_output_dir: str,
        govt_registry_path: Optional[str],
        govt_registry_groups: Optional[List[str]],
    ) -> None:
        session = self._storage.create_session()
        writer = JsonlWriter(output_path, gzip_output=gzip_output, append=True)

        categories = categories or ["Gov", "News"]
        selected = {c.lower() for c in categories}

        registry_path = govt_registry_path
        if not registry_path:
            default = os.path.join("sources", "govt_sources_registry.yaml")
            if os.path.exists(default):
                registry_path = default

        registry_entries = None
        if registry_path:
            registry_entries = load_registry(registry_path, groups=govt_registry_groups)
        allow_default = registry_path is None

        regulatory_entries = [
            e for e in (registry_entries or [])
            if getattr(e, "scraper_class", "") == "regulatory"
        ]

        jobs: List[ScrapeJob] = []
        
        # --- Gov Category Explode ---
        if "gov" in selected or "government" in selected:
            if registry_entries:
                for entry in registry_entries:
                    if entry.scraper_class == "ministry_generic":
                        # Single ministry job
                        from nepali_corpus.core.services.scrapers.govt_scraper import MinistryScraper, post_to_raw, MinistryConfig
                        cfg = MinistryConfig(
                            source_id=entry.source_id,
                            name=entry.name or entry.source_id,
                            name_ne=entry.name_ne or entry.source_id,
                            base_url=entry.base_url,
                            endpoints=entry.endpoints,
                            priority=entry.priority,
                        )
                        jobs.append(
                            ScrapeJob(
                                name=f"gov:{entry.source_id}",
                                category="Gov",
                                func=lambda c=cfg: [post_to_raw(p) for posts in MinistryScraper(c).scrape_all(max_pages_per_endpoint=max_pages or 3).values() for p in posts]
                            )
                        )
                    elif entry.scraper_class == "regulatory":
                        from nepali_corpus.core.services.scrapers.regulatory_scraper import RegulatoryScraper
                        jobs.append(
                            ScrapeJob(
                                name=f"reg:{entry.source_id}",
                                category="Gov",
                                func=lambda e=entry: RegulatoryScraper(e).scrape(pages=max_pages or 1)
                            )
                        )
            
            # DAO Scraper (keep as one job or split?)
            if not govt_registry_groups:
                jobs.append(
                    ScrapeJob(
                        name="dao",
                        category="Gov",
                        func=lambda: dao_scraper.fetch_raw_records(pages=max_pages or 2)
                    )
                )

        # --- Social Category Explode ---
        if "social" in selected:
            from .social_scraper import NitterScraper
            from ..dashboard.sources import load_social_sources
            
            social_sources = load_social_sources()
            # Group by instances or just use one
            scraper = NitterScraper()
            
            for s in social_sources:
                sid = s["id"]
                # Determine if it's a user, hashtag, or search
                if sid.startswith("social:"):
                    username = sid.split(":", 1)[1]
                    jobs.append(
                        ScrapeJob(
                            name=f"social:{username}",
                            category="Social",
                            func=lambda u=username: scraper.fetch_user_tweets(u, max_pages=max_pages or 1)
                        )
                    )
                elif sid.startswith("hashtag:") or sid.startswith("search:"):
                    # Use the URL's query or the name
                    query = s["name"]
                    jobs.append(
                        ScrapeJob(
                            name=f"social_search:{sid}",
                            category="Social",
                            # Use fetch_search_tweets for tag/search
                            func=lambda q=query: scraper.fetch_search_tweets(q, max_pages=max_pages or 1)
                        )
                    )

        if not jobs:
            self.state.add_error("No matching scrapers for selected categories")
            self.state.running = False
            writer.close()
            return

        loop = asyncio.get_running_loop()
        pending: List[asyncio.Future] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for job in jobs:
                # Use a wrapper to return the job instance alongside results
                def _run_job(j=job):
                    try:
                        return j, j.func()
                    except Exception as e:
                        return j, e
                
                future = loop.run_in_executor(pool, _run_job)
                pending.append(future)

            pdf_jobs: List[PdfJob] = []

            for coro in asyncio.as_completed(pending):
                if self._stop_event.is_set():
                    break
                while self.state.paused and not self._stop_event.is_set():
                    await asyncio.sleep(0.5)
                
                try:
                    job, records = await coro
                    if isinstance(records, Exception):
                        raise records
                except Exception as exc:
                    # In this case job might be from the lambda closure, let's be safe
                    # But the _run_job wrapper helps
                    job_name = "unknown"
                    try: job_name = job.name
                    except: pass
                    
                    msg = f"{job_name} failed: {exc}"
                    logger.error(msg)
                    self.state.urls_failed += 1
                    self.state.record_source(job_name, failed=1)
                    self.state.add_error(msg)
                    continue

                self.state.current_sources.append(job.name)
                if len(self.state.current_sources) > 20:
                    self.state.current_sources.pop(0)

                if not records:
                    continue

                seen = 0
                saved_records = []
                for record in records:
                    seen += 1
                    self.state.record_source(record.source_id, crawled=1)
                    try:
                        if await session.seen_url(record.url):
                            continue
                        await session.mark_url(record.url)
                        await session.store_raw_records([record])
                    except Exception as exc:
                        logger.debug("URL dedup unavailable: %s", exc)
                    writer.write(record)
                    saved_records.append(record)
                    self.state.record_source(record.source_id, saved=1)

                    if pdf_enabled:
                        urls = record.raw_meta.get("attachment_urls") if record.raw_meta else None
                        if urls:
                            for url in urls:
                                if url:
                                    pdf_jobs.append(
                                        PdfJob(
                                            url=url,
                                            source_id=record.source_id,
                                            source_name=record.source_name,
                                            category=record.category,
                                        )
                                    )
                
                # Incremental Sync to DB
                if saved_records:
                    try:
                        # Convert RawRecord to TrainingDocument-like structures for the storage session
                        # Or ensure session knows how to handle RawRecord
                        from nepali_corpus.core.models import TrainingDocument
                        training_docs = []
                        for r in saved_records:
                             # Generate a training doc or just pass the data
                             # The storage session uses Mapping[str, Any] or TrainingDocument
                             import hashlib
                             doc_id = hashlib.md5(r.url.encode()).hexdigest()
                             doc = TrainingDocument(
                                 id=doc_id,
                                 url=r.url,
                                 source_id=r.source_id,
                                 source_name=r.source_name,
                                 language=r.language or "ne",
                                 text=r.content or "",
                                 published_at=r.published_at,
                                 date_bs=r.raw_meta.get("date_bs") if r.raw_meta else None,
                                 category=r.category,
                                 tags=[]
                             )
                             training_docs.append(doc)
                        
                        await session.store_training_documents(training_docs)
                        self.state.docs_saved += len(training_docs)
                    except Exception as e:
                        logger.error(f"Failed incremental DB sync: {e}")

                self.state.urls_crawled += seen

        if pdf_enabled and pdf_jobs:
            if not HAS_PYMUPDF:
                self.state.add_error("PyMuPDF not installed; PDF extraction skipped")
            else:
                try:
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
                    self.state.pdf_saved += len(pdf_records)
                    self.state.urls_crawled += len(pdf_records)
                    self.state.docs_saved += len(pdf_records)
                except Exception as exc:
                    self.state.add_error(f"PDF extraction failed: {exc}")

        writer.flush()
        writer.close()
        self.state.running = False


__all__ = ["ScrapeCoordinator", "ScrapeState"]

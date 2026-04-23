"""SQL storage service for Nepali Corpus."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import hashlib
from typing import Any, Dict, Iterable, List, Optional

from pydantic import ConfigDict

from nepali_corpus.core.models import RawRecord, TrainingDocument
from nepali_corpus.core.utils.content_types import identify_content_type
from .db import AsyncDatabase, HAS_ASYNCPG
from .storage import StorageService, StorageSession

STORAGE_AVAILABLE = HAS_ASYNCPG

logger = logging.getLogger("nepali_corpus.storage")


class SQLStorageService(StorageService):
    """Async PostgreSQL storage using asyncpg."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = "postgres"
    db_name: str = "nepali_corpus"
    pool_min: int = 5
    pool_max: int = 50

    schema_path: Optional[str] = None

    _db: Optional[AsyncDatabase] = None
    _is_initialized: bool = False

    def model_post_init(self, __context: object) -> None:
        if self.schema_path is None:
            self.schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        if STORAGE_AVAILABLE and self._db is None:
            class DbConfig:
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)
                    self.retry_delay = 1.0
                    self.retry_max_delay = 10.0
                    self.retry_backoff_factor = 2.0
                    self.retry_jitter = 0.1

            config = DbConfig(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                db_name=self.db_name,
                pool_min=self.pool_min,
                pool_max=self.pool_max,
            )
            self._db = AsyncDatabase(config)

    @staticmethod
    def calculate_pool_size(num_workers: int = 4) -> tuple:
        """Calculate pool size based on worker count.
        
        Each worker needs ~1.5x connections (scraping + enrichment + queries).
        Formula: min = workers * 0.5, max = workers * 2.5
        """
        min_size = max(5, int(num_workers * 0.5))
        max_size = max(20, int(num_workers * 2.5))
        return min_size, max_size
    
    async def initialize(self, num_workers: int = 4) -> None:
        """Initialize storage with dynamic pool sizing.
        
        Args:
            num_workers: Number of scraper workers (used to size connection pool)
        """
        if self._is_initialized:
            return
        if self._db is None:
            if not STORAGE_AVAILABLE:
                raise RuntimeError("Database storage unavailable: asyncpg not installed.")
            raise RuntimeError("Database instance not initialized.")

        # Dynamically size pool based on workers
        min_pool, max_pool = self.calculate_pool_size(num_workers)
        self._db.config.pool_min = min_pool
        self._db.config.pool_max = max_pool
        logger.info(f"Sizing connection pool to {min_pool}-{max_pool} (workers={num_workers})")

        await self._db.initialize()

        if self.schema_path and os.path.exists(self.schema_path):
            with open(self.schema_path, "r", encoding="utf-8") as f:
                schema_sql = f.read()
            try:
                async with self._db.transaction() as conn:
                    await conn.execute(schema_sql)
            except Exception as e:
                logger.debug("Schema apply skipped or failed: %s", e)

        self._is_initialized = True

    async def close(self) -> None:
        if self._db:
            await self._db.close()
        self._is_initialized = False

    def create_session(self) -> "SQLEnvStorageSession":
        return SQLEnvStorageSession(service=self)


class SQLEnvStorageSession(StorageSession):
    service: SQLStorageService

    def _url_hash(self, url: str) -> str:
        return hashlib.md5(url.encode("utf-8")).hexdigest()

    def _scrub(self, val: Any) -> Any:
        """Remove null bytes which PostgreSQL doesn't like in TEXT/JSONB."""
        if isinstance(val, str):
            return val.replace("\x00", "")
        if isinstance(val, dict):
            return {self._scrub(k): self._scrub(v) for k, v in val.items()}
        if isinstance(val, list):
            return [self._scrub(x) for x in val]
        return val
    
    async def _retry_db_operation(self, operation, max_retries: int = 3, base_delay: float = 0.5):
        """Retry a database operation with exponential backoff.
        
        Args:
            operation: Async callable to execute
            max_retries: Maximum number of retry attempts
            base_delay: Initial delay in seconds (exponentially increased)
        
        Returns:
            Result of the operation
        """
        last_exception = None
        for attempt in range(max_retries):
            try:
                return await operation()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"DB operation failed (attempt {attempt+1}/{max_retries}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"DB operation failed after {max_retries} attempts: {e}")
        
        raise last_exception

    _TRAINING_DOC_UPSERT = """
        INSERT INTO training_documents (
            id, url, source_id, source_name, language, text,
            published_at, date_bs, category, content_type, province, district, tags
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (id) DO UPDATE SET
            url = EXCLUDED.url,
            source_id = EXCLUDED.source_id,
            source_name = EXCLUDED.source_name,
            language = EXCLUDED.language,
            text = EXCLUDED.text,
            published_at = EXCLUDED.published_at,
            date_bs = EXCLUDED.date_bs,
            category = EXCLUDED.category,
            content_type = EXCLUDED.content_type,
            province = EXCLUDED.province,
            district = EXCLUDED.district,
            tags = EXCLUDED.tags
    """

    async def store_training_document(self, doc: TrainingDocument) -> str:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")
        
        # Skip documents with null or empty text
        if not doc.text or not doc.text.strip():
            logger.debug(f"Skipping training document {doc.id}: empty or null text")
            return doc.id

        await self.service._db.execute(
            self._TRAINING_DOC_UPSERT,
            doc.id,
            doc.url,
            self._scrub(doc.source_id),
            self._scrub(doc.source_name),
            doc.language,
            self._scrub(doc.text),
            doc.published_at,
            doc.date_bs,
            doc.category,
            doc.content_type or "text/html",
            doc.province,
            doc.district,
            json.dumps(self._scrub(doc.tags)) if doc.tags is not None else None,
        )
        return doc.id

    async def store_training_documents(self, docs: Iterable[TrainingDocument]) -> int:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")

        args_list = []
        skipped = 0
        for doc in docs:
            # Skip documents with null or empty text
            if not doc.text or not doc.text.strip():
                skipped += 1
                logger.debug(f"Skipping training document {doc.id}: empty or null text")
                continue
            
            args_list.append(
                (
                    doc.id,
                    doc.url,
                    self._scrub(doc.source_id),
                    self._scrub(doc.source_name),
                    doc.language,
                    self._scrub(doc.text),
                    doc.published_at,
                    doc.date_bs,
                    doc.category,
                    doc.content_type or "text/html",
                    doc.province,
                    doc.district,
                    json.dumps(self._scrub(doc.tags)) if doc.tags is not None else None,
                )
            )
        
        if skipped > 0:
            logger.info(f"Skipped {skipped} documents with empty/null text")
        
        if not args_list:
            return 0
        
        await self.service._db.executemany(self._TRAINING_DOC_UPSERT, args_list)
        return len(args_list)

    async def store_raw_records(self, records: Iterable[RawRecord]) -> int:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")

        query = """
            INSERT INTO raw_records (
                url, source_id, source_name, title, summary, content,
                language, published_at, date_bs, category, content_type, fetched_at, raw_meta
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            ON CONFLICT (url) DO UPDATE SET
                source_id = EXCLUDED.source_id,
                source_name = EXCLUDED.source_name,
                title = EXCLUDED.title,
                summary = EXCLUDED.summary,
                content = EXCLUDED.content,
                language = EXCLUDED.language,
                published_at = EXCLUDED.published_at,
                date_bs = EXCLUDED.date_bs,
                category = EXCLUDED.category,
                content_type = EXCLUDED.content_type,
                fetched_at = EXCLUDED.fetched_at,
                raw_meta = EXCLUDED.raw_meta
        """
        args_list = []
        from ...utils.normalize import devanagari_ratio

        for rec in records:
            # Auto-detect language if missing/suspicious for raw records too
            lang = rec.language
            check_text = (rec.title or "") + " " + (rec.content or "") + " " + (rec.summary or "")
            if len(check_text.strip()) > 20:
                lang = "ne" if devanagari_ratio(check_text) >= 0.15 else "en"
            
            args_list.append(
                (
                    rec.url,
                    rec.source_id,
                    self._scrub(rec.source_name),
                    self._scrub(rec.title),
                    self._scrub(rec.summary),
                    self._scrub(rec.content),
                    lang,
                    rec.published_at,
                    rec.date_bs,
                    rec.category,
                    rec.content_type or identify_content_type(rec.url),
                    rec.fetched_at,
                    json.dumps(self._scrub(rec.raw_meta)) if rec.raw_meta is not None else None,
                )
            )

        if not args_list:
            return 0
        
        async def _execute():
            await self.service._db.executemany(query, args_list)
        
        await self._retry_db_operation(_execute)
        return len(args_list)

    async def list_recent_documents(self, limit: int = 50):
        if self.service._db is None:
            raise RuntimeError("Database unavailable")
        query = """
            SELECT id, url, source_id, source_name, language, text,
                   published_at, date_bs, category, province, district, tags, created_at
            FROM training_documents
            ORDER BY created_at DESC
            LIMIT $1
        """
        rows = await self.service._db.fetch(query, limit)
        return [dict(row) for row in rows]

    async def get_stats(self) -> dict:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")

        total = await self.service._db.fetch_value("SELECT COUNT(*) FROM training_documents")
        by_source_rows = await self.service._db.fetch(
            "SELECT source_id, COUNT(*) FROM training_documents GROUP BY source_id"
        )
        by_language_rows = await self.service._db.fetch(
            "SELECT language, COUNT(*) FROM training_documents GROUP BY language"
        )

        return {
            "total_documents": int(total or 0),
            "by_source": {row[0]: int(row[1]) for row in by_source_rows},
            "by_language": {row[0]: int(row[1]) for row in by_language_rows},
        }

    async def seen_url(self, url: str) -> bool:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")
        url_hash = self._url_hash(url)
        row = await self.service._db.fetch_one(
            "SELECT 1 FROM visited_urls WHERE url_hash = $1",
            url_hash,
        )
        return row is not None

    async def mark_url(self, url: str) -> None:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")
        url_hash = self._url_hash(url)
        await self.service._db.execute(
            "INSERT INTO visited_urls (url_hash, url) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            url_hash,
            url,
        )

    async def seen_urls_batch(self, urls: list) -> set:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")
        hashes = [self._url_hash(u) for u in urls]
        rows = await self.service._db.fetch(
            "SELECT url_hash FROM visited_urls WHERE url_hash = ANY($1)", hashes
        )
        seen_hashes = {row["url_hash"] for row in rows}
        return {u for u in urls if self._url_hash(u) in seen_hashes}

    async def mark_urls_batch(self, urls: list) -> None:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")
        args = [(self._url_hash(u), u) for u in urls if u]
        if args:
            await self.service._db.executemany(
                "INSERT INTO visited_urls (url_hash, url) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                args,
            )

    async def count_urls(self) -> int:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")
        total = await self.service._db.fetch_value("SELECT COUNT(*) FROM visited_urls")
        return int(total or 0)

    # --- Pipeline run tracking ---

    async def create_pipeline_run(
        self,
        run_id: str,
        sources: Optional[List[str]] = None,
        categories: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        output_dir: Optional[str] = None,
    ) -> int:
        if self.service._db is None:
            return 0
        row = await self.service._db.fetch_one(
            """
            INSERT INTO pipeline_runs (run_id, sources_requested, categories, config_json, output_dir, status)
            VALUES ($1, $2, $3, $4, $5, 'running')
            RETURNING id
            """,
            run_id,
            sources or [],
            categories or [],
            json.dumps(config or {}),
            output_dir,
        )
        return row[0] if row else 0

    # Frozen sets prevent SQL injection via dynamic column names
    _PIPELINE_RUN_COLUMNS = frozenset({
        "status", "completed_jobs", "failed_jobs", "total_jobs",
        "total_records_scraped", "total_records_saved", "completed_at",
    })
    _PIPELINE_JOB_COLUMNS = frozenset({
        "status", "error_message", "records_crawled", "records_saved",
        "records_failed", "pages_scraped", "last_url", "attempt_number",
        "started_at", "completed_at", "duration_ms",
    })

    async def update_pipeline_run(self, run_id: str, **kwargs: Any) -> None:
        if self.service._db is None:
            return
        sets, args = [], []
        idx = 1
        for key, val in kwargs.items():
            if key not in self._PIPELINE_RUN_COLUMNS:
                continue
            # Validate column name contains only safe chars (defense in depth)
            if not key.isidentifier():
                continue
            idx += 1
            sets.append(f"{key} = ${idx}")
            args.append(val)
        if not sets:
            return
        sets.append("updated_at = CURRENT_TIMESTAMP")
        query = f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE run_id = $1"
        await self.service._db.execute(query, run_id, *args)

    async def create_pipeline_job(
        self,
        pipeline_run_id: int,
        job_type: str,
        source_id: str,
        source_name: Optional[str] = None,
        category: Optional[str] = None,
        scraper_class: Optional[str] = None,
    ) -> int:
        if self.service._db is None:
            return 0
        row = await self.service._db.fetch_one(
            """
            INSERT INTO pipeline_jobs
                (pipeline_run_id, job_type, source_id, source_name, category, scraper_class)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            pipeline_run_id, job_type, source_id, source_name, category, scraper_class,
        )
        return row[0] if row else 0

    async def update_pipeline_job(self, job_id: int, **kwargs: Any) -> None:
        if self.service._db is None:
            return
        sets, args = [], []
        idx = 1
        for key, val in kwargs.items():
            if key not in self._PIPELINE_JOB_COLUMNS:
                continue
            if not key.isidentifier():
                continue
            idx += 1
            sets.append(f"{key} = ${idx}")
            args.append(val)
        if not sets:
            return
        sets.append("updated_at = CURRENT_TIMESTAMP")
        query = f"UPDATE pipeline_jobs SET {', '.join(sets)} WHERE id = $1"
        await self.service._db.execute(query, job_id, *args)

    async def get_pending_jobs(self, run_id: str, job_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.service._db is None:
            return []
        base = """
            SELECT pj.* FROM pipeline_jobs pj
            JOIN pipeline_runs pr ON pr.id = pj.pipeline_run_id
            WHERE pr.run_id = $1
              AND pj.status IN ('pending', 'interrupted')
              AND pj.attempt_number <= pj.max_attempts
        """
        if job_type:
            base += " AND pj.job_type = $2 ORDER BY pj.id"
            rows = await self.service._db.fetch(base, run_id, job_type)
        else:
            base += " ORDER BY pj.id"
            rows = await self.service._db.fetch(base, run_id)
        return [dict(r) for r in rows]

    async def get_run_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        if self.service._db is None:
            return None
        row = await self.service._db.fetch_one(
            "SELECT * FROM pipeline_runs WHERE run_id = $1", run_id
        )
        if not row:
            return None
        result = dict(row)
        # Add job breakdown
        job_rows = await self.service._db.fetch(
            """
            SELECT job_type, status, COUNT(*) as cnt,
                   COALESCE(SUM(records_crawled), 0) as total_crawled,
                   COALESCE(SUM(records_saved), 0) as total_saved,
                   COALESCE(SUM(records_failed), 0) as total_failed
            FROM pipeline_jobs
            WHERE pipeline_run_id = $1
            GROUP BY job_type, status
            """,
            result["id"],
        )
        result["jobs_breakdown"] = [dict(r) for r in job_rows]
        return result

    async def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        if self.service._db is None:
            return []
        rows = await self.service._db.fetch(
            """
            SELECT id, run_id, sources_requested, categories, status,
                   total_jobs, completed_jobs, failed_jobs,
                   total_records_scraped, total_records_saved,
                   output_dir, started_at, completed_at
            FROM pipeline_runs
            ORDER BY started_at DESC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]


class EnvStorageService(SQLStorageService):
    """Storage service configured via environment variables."""

    def model_post_init(self, __context: object) -> None:
        self.host = os.getenv("DB_HOST", self.host)
        self.port = int(os.getenv("DB_PORT", self.port))
        self.user = os.getenv("DB_USER", self.user)
        self.password = os.getenv("DB_PASSWORD", self.password)
        self.db_name = os.getenv("DB_NAME", self.db_name)
        super().model_post_init(__context)


__all__ = [
    "SQLStorageService",
    "EnvStorageService",
    "SQLEnvStorageSession",
    "STORAGE_AVAILABLE",
]

"""SQL storage service for Nepali Corpus."""
from __future__ import annotations

import logging
import os
import hashlib
from typing import Iterable, Optional

from pydantic import ConfigDict

from nepali_corpus.core.models import TrainingDocument
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
    pool_min: int = 2
    pool_max: int = 10

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

    async def initialize(self) -> None:
        if self._is_initialized:
            return
        if self._db is None:
            if not STORAGE_AVAILABLE:
                raise RuntimeError("Database storage unavailable: asyncpg not installed.")
            raise RuntimeError("Database instance not initialized.")

        await self._db.initialize()

        if self.schema_path and os.path.exists(self.schema_path):
            with open(self.schema_path, "r", encoding="utf-8") as f:
                schema_sql = f.read()
            try:
                async with self._db.safe_transaction() as conn:
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

    async def store_training_document(self, doc: TrainingDocument) -> str:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")

        query = """
            INSERT INTO training_documents (
                id, url, source_id, source_name, language, text,
                published_at, date_bs, category, province, district, tags
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (id) DO UPDATE SET
                url = EXCLUDED.url,
                source_id = EXCLUDED.source_id,
                source_name = EXCLUDED.source_name,
                language = EXCLUDED.language,
                text = EXCLUDED.text,
                published_at = EXCLUDED.published_at,
                date_bs = EXCLUDED.date_bs,
                category = EXCLUDED.category,
                province = EXCLUDED.province,
                district = EXCLUDED.district,
                tags = EXCLUDED.tags
        """
        import json
        await self.service._db.execute(
            query,
            doc.id,
            doc.url,
            doc.source_id,
            doc.source_name,
            doc.language,
            doc.text,
            doc.published_at,
            doc.date_bs,
            doc.category,
            doc.province,
            doc.district,
            json.dumps(doc.tags) if doc.tags is not None else None,
        )
        return doc.id

    def _scrub(self, val: Any) -> Any:
        """Remove null bytes which PostgreSQL doesn't like in TEXT/JSONB."""
        if isinstance(val, str):
            return val.replace("\x00", "")
        if isinstance(val, dict):
            return {self._scrub(k): self._scrub(v) for k, v in val.items()}
        if isinstance(val, list):
            return [self._scrub(x) for x in val]
        return val

    async def store_training_documents(self, docs: Iterable[TrainingDocument]) -> int:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")

        query = """
            INSERT INTO training_documents (
                id, url, source_id, source_name, language, text,
                published_at, date_bs, category, province, district, tags
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (id) DO UPDATE SET
                url = EXCLUDED.url,
                source_id = EXCLUDED.source_id,
                source_name = EXCLUDED.source_name,
                language = EXCLUDED.language,
                text = EXCLUDED.text,
                published_at = EXCLUDED.published_at,
                date_bs = EXCLUDED.date_bs,
                category = EXCLUDED.category,
                province = EXCLUDED.province,
                district = EXCLUDED.district,
                tags = EXCLUDED.tags
        """
        args_list = []
        import json
        for doc in docs:
            args_list.append(
                (
                    doc.id,
                    doc.url,
                    doc.source_id,
                    self._scrub(doc.source_name),
                    doc.language,
                    self._scrub(doc.text),
                    doc.published_at,
                    doc.date_bs,
                    doc.category,
                    doc.province,
                    doc.district,
                    json.dumps(self._scrub(doc.tags)) if doc.tags is not None else None,
                )
            )
        if not args_list:
            return 0
        await self.service._db.executemany(query, args_list)
        return len(args_list)

    async def store_raw_records(self, records: Iterable[RawRecord]) -> int:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")

        query = """
            INSERT INTO raw_records (
                url, source_id, source_name, title, summary, content,
                language, published_at, date_bs, category, fetched_at, raw_meta
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
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
                fetched_at = EXCLUDED.fetched_at,
                raw_meta = EXCLUDED.raw_meta
        """
        args_list = []
        import json
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
                    rec.fetched_at,
                    json.dumps(self._scrub(rec.raw_meta)) if rec.raw_meta is not None else None,
                )
            )
            # Ensure URL is marked as visited in the same flow
            try:
                await self.mark_url(rec.url)
            except Exception:
                pass

        if not args_list:
            return 0
        await self.service._db.executemany(query, args_list)
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

    async def count_urls(self) -> int:
        if self.service._db is None:
            raise RuntimeError("Database unavailable")
        total = await self.service._db.fetch_value("SELECT COUNT(*) FROM visited_urls")
        return int(total or 0)


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

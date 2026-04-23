"""Async database wrapper for Nepali Corpus."""
from __future__ import annotations

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, List, Optional

try:
    import asyncpg
    from asyncpg import Pool, create_pool
    from asyncpg.exceptions import DuplicateDatabaseError
    HAS_ASYNCPG = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_ASYNCPG = False

logger = logging.getLogger("nepali_corpus.storage.db")


class AsyncDatabase:
    """Asynchronous PostgreSQL wrapper using asyncpg."""

    def __init__(self, config: Any):
        self.config = config
        self.pool: Optional[Pool] = None
        self._lock = asyncio.Lock()
        self._is_initialized = False

        self.retry_config = {
            "initial_delay": getattr(self.config, "retry_delay", 1.0),
            "max_delay": getattr(self.config, "retry_max_delay", 10.0),
            "backoff_factor": getattr(self.config, "retry_backoff_factor", 2.0),
            "jitter": getattr(self.config, "retry_jitter", 0.1),
        }

    async def initialize(self) -> None:
        if not HAS_ASYNCPG:
            raise ImportError("asyncpg is required for AsyncDatabase")

        if self._is_initialized:
            return

        async with self._lock:
            if self._is_initialized:
                return

            await self._ensure_database_exists()

            self.pool = await create_pool(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                database=self.config.db_name,
                min_size=getattr(self.config, "pool_min", 2),
                max_size=getattr(self.config, "pool_max", 10),
                timeout=30,
                command_timeout=60,
            )
            self._is_initialized = True
            logger.info("Database pool initialized for %s", self.config.db_name)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None
        self._is_initialized = False

    async def is_connected(self) -> bool:
        try:
            if not self.pool:
                return False
            async with self.pool.acquire(timeout=15.0) as conn:
                await conn.execute("SELECT 1")
                return True
        except Exception as e:
            logger.error("Database connection check failed: %s", e)
            return False

    async def _ensure_database_exists(self) -> None:
        try:
            temp_conn = await asyncpg.connect(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                database="postgres",
            )
            try:
                safe_name = self.config.db_name.replace('"', '""')
                await temp_conn.execute(f'CREATE DATABASE "{safe_name}"')
                logger.info("Created database %s", self.config.db_name)
            except DuplicateDatabaseError:
                pass
            finally:
                await temp_conn.close()
        except Exception as e:
            import re as _re
            safe_msg = _re.sub(r'password=[^\s)]+', 'password=***', str(e))
            logger.error("Database creation failed: %s", safe_msg)

    async def execute(self, query: str, *args: Any) -> str:
        if not self.pool:
            await self.initialize()
        async with self.pool.acquire(timeout=15.0) as conn:
            return await conn.execute(query, *args)

    async def executemany(self, query: str, args_list: List[tuple]) -> None:
        if not self.pool:
            await self.initialize()
        async with self.pool.acquire(timeout=15.0) as conn:
            await conn.executemany(query, args_list)

    async def fetch(self, query: str, *args: Any):
        if not self.pool:
            await self.initialize()
        async with self.pool.acquire(timeout=15.0) as conn:
            return await conn.fetch(query, *args)

    async def fetch_one(self, query: str, *args: Any):
        if not self.pool:
            await self.initialize()
        async with self.pool.acquire(timeout=15.0) as conn:
            return await conn.fetchrow(query, *args)

    async def fetch_value(self, query: str, *args: Any):
        row = await self.fetch_one(query, *args)
        if row is None:
            return None
        return row[0]

    @asynccontextmanager
    async def transaction(self, isolation: str = "repeatable_read") -> AsyncIterator[asyncpg.Connection]:
        if not self.pool:
            await self.initialize()
        async with self.pool.acquire(timeout=15.0) as conn:
            async with conn.transaction(isolation=isolation):
                yield conn

    async def safe_execute(self, query: str, *args: Any, max_retries: int = 3) -> str:
        """Execute a query with retry logic and exponential backoff."""
        last_error = None
        for attempt in range(max_retries):
            try:
                async with self.transaction() as conn:
                    return await conn.execute(query, *args)
            except Exception as e:
                last_error = e
                logger.error("Transaction failed (attempt %s/%s): %s", attempt + 1, max_retries, e)
                if attempt < max_retries - 1:
                    await asyncio.sleep(self._calculate_backoff(attempt))
        raise last_error

    def _calculate_backoff(self, attempt: int) -> float:
        current_delay = self.retry_config["initial_delay"] * (
            self.retry_config["backoff_factor"] ** attempt
        )
        jitter_amount = current_delay * self.retry_config["jitter"] * random.uniform(-1, 1)
        next_delay = current_delay + jitter_amount
        return max(0, min(next_delay, self.retry_config["max_delay"]))

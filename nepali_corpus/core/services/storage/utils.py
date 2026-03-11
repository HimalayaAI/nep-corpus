"""Utilities for corpus database management."""
from __future__ import annotations

import asyncio
import logging

from .env_storage import EnvStorageService, STORAGE_AVAILABLE


async def setup_corpus_db() -> bool:
    if not STORAGE_AVAILABLE:
        print("Error: asyncpg not available. Cannot setup database.")
        return False

    storage = EnvStorageService()
    try:
        await storage.initialize()
        print("Successfully initialized Nepali Corpus database")
        await storage.close()
        return True
    except Exception as e:
        print(f"Failed to setup database: {e}")
        return False


async def check_database_status() -> None:
    if not STORAGE_AVAILABLE:
        print("Error: asyncpg not available.")
        return

    storage = EnvStorageService()
    try:
        await storage.initialize()
        session = storage.create_session()
        stats = await session.get_stats()
        print("\n--- Nepali Corpus Database Stats ---")
        print(f"Total documents: {stats['total_documents']}")
        print("By source:")
        for k, v in stats["by_source"].items():
            print(f"  {k}: {v}")
        print("By language:")
        for k, v in stats["by_language"].items():
            print(f"  {k}: {v}")
        await storage.close()
    except Exception as e:
        print(f"Database check failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "check":
        asyncio.run(check_database_status())
    else:
        asyncio.run(setup_corpus_db())

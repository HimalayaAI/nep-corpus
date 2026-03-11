#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nepali_corpus.core.services.storage.env_storage import EnvStorageService, STORAGE_AVAILABLE


async def _init_db() -> int:
    if not STORAGE_AVAILABLE:
        print("asyncpg is not installed; cannot initialize database.", file=sys.stderr)
        return 1

    storage = EnvStorageService()
    try:
        await storage.initialize()
    except Exception as exc:  # pragma: no cover - surface errors to caller
        print(f"Database initialization failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await storage.close()

    print("Database schema initialized.")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_init_db()))


if __name__ == "__main__":
    main()

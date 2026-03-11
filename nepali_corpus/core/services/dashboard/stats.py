from __future__ import annotations


def ensure_dict(value):
    return value if isinstance(value, dict) else dict(value)


async def fetch_stats(session) -> dict:
    return await session.get_stats()

from __future__ import annotations

from typing import Iterable, List, Set

from ..models import NormalizedDocument


def deduplicate(docs: Iterable[NormalizedDocument]) -> List[NormalizedDocument]:
    seen_urls: Set[str] = set()
    seen_keys: Set[str] = set()
    unique: List[NormalizedDocument] = []

    for doc in docs:
        if doc.url in seen_urls:
            continue
        if doc.dedup_key and doc.dedup_key in seen_keys:
            continue
        seen_urls.add(doc.url)
        if doc.dedup_key:
            seen_keys.add(doc.dedup_key)
        unique.append(doc)
    return unique

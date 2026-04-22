from __future__ import annotations

from typing import Iterable, List, Set

from ..models import NormalizedDocument


def deduplicate(docs: Iterable[NormalizedDocument]) -> List[NormalizedDocument]:
    seen_urls: Set[str] = set()
    seen_keys: Set[str] = set()
    unique: List[NormalizedDocument] = []

    for doc in docs:
        # Skip documents with duplicate URL
        if doc.url in seen_urls:
            continue
        
        # Only use dedup_key if non-empty after stripping
        if doc.dedup_key and doc.dedup_key.strip():
            normalized_key = doc.dedup_key.strip()
            if normalized_key in seen_keys:
                continue
            seen_keys.add(normalized_key)
        
        seen_urls.add(doc.url)
        unique.append(doc)
    
    return unique

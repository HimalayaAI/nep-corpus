"""Source catalog for the dashboard."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from nepali_corpus.core.services.scrapers.source_registry import SourceRegistry


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


@lru_cache(maxsize=1)
def load_source_catalog() -> List[Dict[str, Any]]:
    """Load all sources from the unified registry."""
    reg = SourceRegistry(str(_repo_root() / "sources"))
    reg.load_all()
    
    sources: List[Dict[str, Any]] = []
    for cfg in reg.all_sources():
        group = cfg.category or cfg.source_type
        sources.append({
            "id": cfg.id,
            "name": cfg.name,
            "url": cfg.url,
            "category": cfg.source_type.title() if cfg.source_type else "Unknown",
            "group": group,
            "group_label": group.replace("_", " ").title() if group else "General",
            "source_type": cfg.source_type.title() if cfg.source_type else "Unknown",
            "format": cfg.source_type.upper() if cfg.source_type in ["rss", "html", "api"] else cfg.source_type.title(),
        })
        
    # Make sure we sort them somewhat predictably
    sources.sort(key=lambda x: (x["source_type"], x["group"], x["name"]))
    return sources


def get_sources(refresh: bool = False) -> List[Dict[str, Any]]:
    """Get all sources, optionally refreshing the cache."""
    if refresh:
        load_source_catalog.cache_clear()
    # Return copies to prevent mutation issues
    return [dict(item) for item in load_source_catalog()]


__all__ = ["get_sources"]

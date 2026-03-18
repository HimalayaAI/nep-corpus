"""Unified Source Registry — auto-discovers YAML/JSONL source files.

Reads all ``*.yaml`` and ``*.jsonl`` files under a given directory, parses
each entry as a :class:`SourceConfig`, and provides filtered lookups.

All source files must use the unified ``SourceConfig`` schema (flat lists).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from nepali_corpus.core.models.source_config import SourceConfig

logger = logging.getLogger(__name__)


class SourceRegistry:
    """Auto-discovers and loads YAML/JSONL source files from a directory.

    All files must use the unified SourceConfig schema — flat lists of
    source entries.

    Usage::

        registry = SourceRegistry("sources")
        registry.load_all()

        rss_sources = registry.list(source_type="rss")
        govt_sources = registry.list(source_type="government")
    """

    def __init__(self, sources_dir: str = "sources") -> None:
        self._sources_dir = sources_dir
        self._sources: Dict[str, SourceConfig] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_all(self) -> None:
        """Walk ``sources_dir`` and load every ``.yaml`` and ``.jsonl`` file."""
        self._sources.clear()
        root = Path(self._sources_dir)
        if not root.is_dir():
            logger.warning("Sources directory does not exist: %s", self._sources_dir)
            return

        # Sort files for deterministic load order (alphabetical)
        files = sorted(root.rglob("*"))
        for fp in files:
            if fp.suffix in (".yaml", ".yml"):
                self._load_yaml(fp)
            elif fp.suffix == ".jsonl":
                self._load_jsonl(fp)
            # Deliberately skip .json files (archival data)

    def _load_yaml(self, path: Path) -> None:
        """Load a YAML file containing a flat list of SourceConfig entries."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as exc:
            logger.error("Failed to load YAML %s: %s", path, exc)
            return

        if data is None:
            return

        # Expect a flat list of source dicts
        if isinstance(data, list):
            raw_entries = [item for item in data if isinstance(item, dict)]
        else:
            logger.warning(
                "Expected a flat list in %s, got %s — skipping",
                path,
                type(data).__name__,
            )
            return

        loaded = 0
        for raw in raw_entries:
            try:
                cfg = SourceConfig(**raw)
                self._sources[cfg.id] = cfg
                loaded += 1
            except Exception as exc:
                logger.debug("Skipping invalid entry in %s: %s", path, exc)

        if loaded:
            logger.info("Loaded %d sources from %s", loaded, path)

    def _load_jsonl(self, path: Path) -> None:
        """Load a JSONL file (one JSON object per line)."""
        loaded = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                        cfg = SourceConfig(**raw)
                        self._sources[cfg.id] = cfg
                        loaded += 1
                    except Exception as exc:
                        logger.debug("Skipping line %d in %s: %s", line_no, path, exc)
        except Exception as exc:
            logger.error("Failed to load JSONL %s: %s", path, exc)
            return

        if loaded:
            logger.info("Loaded %d sources from %s", loaded, path)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, source_id: str) -> Optional[SourceConfig]:
        """Return a single source by its ``id``, or ``None``."""
        return self._sources.get(source_id)

    def list(
        self,
        *,
        source_type: Optional[str] = None,
        language: Optional[str] = None,
        enabled_only: bool = True,
        scraper_class: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[SourceConfig]:
        """Return sources matching the given filters."""
        results: List[SourceConfig] = []
        for cfg in self._sources.values():
            if enabled_only and not cfg.is_enabled:
                continue
            if source_type is not None and cfg.source_type != source_type:
                continue
            if language is not None and cfg.language != language:
                continue
            if scraper_class is not None and cfg.scraper_class != scraper_class:
                continue
            if category is not None and cfg.category != category:
                continue
            results.append(cfg)
        return results

    def all_sources(self) -> List[SourceConfig]:
        """Return all loaded sources (including disabled)."""
        return list(self._sources.values())

    @property
    def count(self) -> int:
        """Total number of loaded sources."""
        return len(self._sources)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def to_feeds_dict(self) -> Dict[str, Dict[str, Any]]:
        """Convert RSS sources to the legacy FEEDS dict format.

        Backward-compat helper for code that still expects the
        ``{feed_id: {name, url, language, priority}}`` shape.
        """
        feeds: Dict[str, Dict[str, Any]] = {}
        for cfg in self.list(source_type="rss"):
            feeds[cfg.id] = {
                "name": cfg.name,
                "url": cfg.url,
                "language": cfg.language,
                "priority": cfg.effective_priority,
            }
        return feeds


__all__ = ["SourceRegistry"]

"""Unified source configuration model.

A single Pydantic model that works for all source types (news, govt, social,
api, etc.).  Contributors only need to provide ``id``, ``name``, and ``url``;
every other field is optional and source-type-specific.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .base import BaseEntity


class SourceConfig(BaseEntity):
    """Unified source definition — works for news, govt, social, any domain.

    Required fields (every contributor provides these):
        id   – unique key, e.g. ``"onlinekhabar_ne"``
        name – human-readable display name
        url  – base URL or feed URL

    Everything else is optional and source-type-specific.
    """

    # === REQUIRED ===
    id: str
    name: str
    url: str

    # === OPTIONAL — general metadata ===
    source_type: str = "html"  # rss | html | government | social | api
    language: str = "ne"  # ne | en | mixed
    district: Optional[str] = None
    province: Optional[str] = None
    category: Optional[str] = None  # national | provincial | business | ...
    tags: List[str] = Field(default_factory=list)
    name_ne: Optional[str] = None

    # === OPTIONAL — scraping config (system / power-user) ===
    scraper_class: Optional[str] = None  # e.g. ministry_generic, nitter
    is_discovery: bool = False
    endpoints: Optional[Dict[str, str]] = None  # govt: {press_release: /path}
    priority: Optional[int] = None  # 1-5, runtime default: 3
    poll_interval_mins: Optional[int] = None  # scheduled sources only
    enabled: Optional[bool] = None  # set False to disable; None = enabled

    # === Catch-all for anything else ===
    meta: Dict[str, Any] = Field(default_factory=dict)  # reg_date, sn, …

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def is_enabled(self) -> bool:
        """Return effective enabled state (``None`` is treated as enabled)."""
        return self.enabled is not False

    @property
    def effective_priority(self) -> int:
        """Return effective priority (``None`` → 3)."""
        if self.priority is not None:
            return self.priority
        return 3


__all__ = ["SourceConfig"]

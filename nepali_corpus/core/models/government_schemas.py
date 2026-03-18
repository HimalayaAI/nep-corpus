from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import Field

from .base import (
    BaseEntity,
    ContentEntity,
    NepalLocatableMixin,
    TimestampMixin,
)


class GovtPost(ContentEntity, NepalLocatableMixin, TimestampMixin):
    """A single government post (press release, notice, news, etc.).

    Inherits from:
        ContentEntity        – source_id, source_name, url, title, language,
                               category, content_type
        NepalLocatableMixin  – province, district, date_bs
        TimestampMixin       – scraped_at
    """

    id: str
    source_domain: str
    date_ad: Optional[datetime] = None
    category: str = "press-release"
    language: str = "en"
    has_attachment: bool = False
    attachment_urls: List[str] = Field(default_factory=list)
    content_snippet: Optional[str] = None


class MinistryConfig(BaseEntity):
    """Configuration for a ministry scraper.

    Inherits from:
        BaseEntity – common model config
    """

    source_id: str
    name: str
    name_ne: str
    base_url: str
    endpoints: Dict[str, str] = Field(default_factory=dict)
    page_structure: str = "category"  # category | table | list | card
    priority: int = 2
    poll_interval_mins: int = 60


class RegistryEntry(BaseEntity):
    """A registry entry describing a government source to scrape."""

    source_id: Optional[str] = None
    name: Optional[str] = None
    name_ne: Optional[str] = None
    base_url: Optional[str] = None
    endpoints: Dict[str, str] = Field(default_factory=dict)
    scraper_class: str = ""
    is_discovery: bool = False
    priority: int = 3
    poll_interval_mins: int = 180


class DAOPost(ContentEntity, NepalLocatableMixin, TimestampMixin):
    """A post/notice from a District Administration Office.

    Inherits from:
        ContentEntity        – source_id, source_name, url, title, language,
                               category, content_type
        NepalLocatableMixin  – province, district, date_bs
        TimestampMixin       – scraped_at
    """

    id: str
    category: str = "notice"
    has_attachment: bool = False
    source: str = ""


__all__ = [
    "GovtPost",
    "MinistryConfig",
    "RegistryEntry",
    "DAOPost",
]

"""Base entity models for the Nepali Corpus pipeline.

Provides a composable hierarchy of Pydantic base classes that capture
shared fields across scraped content, documents, and pipeline artefacts.

Hierarchy
---------
BaseEntity              – frozen config, common model settings
CorpusEntity            – source_id, source_name  (root for all source-linked entities)
ContentEntity           – url, title, language, category, content_type (web content)
NepalLocatableMixin     – province, district, date_bs (Nepal-specific geography/calendar)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Root base
# ---------------------------------------------------------------------------


class BaseEntity(BaseModel):
    """Absolute base for every entity in the corpus system.

    Sets project-wide Pydantic model configuration.
    """

    model_config = ConfigDict(
        # Allow population by field name or alias
        populate_by_name=True,
        # Strip leading/trailing whitespace from strings
        str_strip_whitespace=True,
    )


# ---------------------------------------------------------------------------
# Core composable bases
# ---------------------------------------------------------------------------


class CorpusEntity(BaseEntity):
    """Any entity linked to a named data source."""

    source_id: str
    source_name: str


class ContentEntity(CorpusEntity):
    """A piece of web content tied to a URL."""

    url: str
    title: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None
    content_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------


class NepalLocatableMixin(BaseEntity):
    """Mixin that adds Nepal-specific location and calendar fields."""

    province: Optional[str] = None
    district: Optional[str] = None
    date_bs: Optional[str] = None  # Bikram Sambat date (e.g. "2081-09-15")


class TimestampMixin(BaseEntity):
    """Mixin that adds a scrape/fetch timestamp."""

    scraped_at: datetime = Field(default_factory=datetime.utcnow)


class TagsMixin(BaseEntity):
    """Mixin that adds a tags list."""

    tags: List[str] = Field(default_factory=list)


class MetadataMixin(BaseEntity):
    """Mixin that carries an arbitrary raw metadata dict."""

    raw_meta: Dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "BaseEntity",
    "CorpusEntity",
    "ContentEntity",
    "NepalLocatableMixin",
    "TimestampMixin",
    "TagsMixin",
    "MetadataMixin",
]

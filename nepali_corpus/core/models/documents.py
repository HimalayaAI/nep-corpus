from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from .base import (
    ContentEntity,
    MetadataMixin,
    NepalLocatableMixin,
    TagsMixin,
)


class RawRecord(ContentEntity, NepalLocatableMixin, TagsMixin, MetadataMixin):
    """A single raw record ingested from any scraper.

    Inherits from:
        ContentEntity        – source_id, source_name, url, title, language,
                               category, content_type
        NepalLocatableMixin  – province, district, date_bs
        TagsMixin            – tags
        MetadataMixin        – raw_meta
    """

    summary: Optional[str] = None
    content: Optional[str] = None
    published_at: Optional[str] = None
    fetched_at: Optional[str] = None


class NormalizedDocument(ContentEntity, NepalLocatableMixin, TagsMixin, MetadataMixin):
    """A cleaned and normalized document ready for deduplication.

    Inherits from:
        ContentEntity        – source_id, source_name, url, title, language,
                               category, content_type
        NepalLocatableMixin  – province, district, date_bs
        TagsMixin            – tags
        MetadataMixin        – raw_meta
    """

    id: str
    text: str
    language: str  # override Optional → required
    published_at: Optional[str] = None
    dedup_key: Optional[str] = None


class TrainingDocument(ContentEntity, NepalLocatableMixin, TagsMixin):
    """A final training-ready document for the corpus.

    Inherits from:
        ContentEntity        – source_id, source_name, url, title, language,
                               category, content_type
        NepalLocatableMixin  – province, district, date_bs
        TagsMixin            – tags
    """

    id: str
    text: str
    language: str  # override Optional → required
    published_at: Optional[str] = None

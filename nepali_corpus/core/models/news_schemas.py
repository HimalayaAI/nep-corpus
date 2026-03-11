from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import List, Optional

from pydantic import Field

from .base import ContentEntity, TimestampMixin


class EkantipurArticle(ContentEntity, TimestampMixin):
    """An Ekantipur news article.

    Inherits from:
        ContentEntity   – source_id, source_name, url, title, language,
                          category, content_type
        TimestampMixin  – scraped_at
    """

    id: str = ""
    province: str
    published_at: Optional[str] = None
    image_url: Optional[str] = None
    summary: Optional[str] = None
    language: str = "ne"

    def model_post_init(self, __context: object) -> None:
        if self.id:
            return
        id_match = re.search(r"-(\d+)(?:\.html)?$", self.url)
        if id_match:
            self.id = f"ekantipur_{id_match.group(1)}"
        else:
            self.id = f"ekantipur_{hashlib.md5(self.url.encode()).hexdigest()[:12]}"


class RssArticle(ContentEntity, TimestampMixin):
    """A news article from an RSS feed.

    Inherits from:
        ContentEntity   – source_id, source_name, url, title, language,
                          category, content_type
        TimestampMixin  – scraped_at  (aliased as fetched_at below)
    """

    id: str
    language: str  # override Optional → required
    published_at: Optional[str] = None
    summary: Optional[str] = None
    content: Optional[str] = None
    author: Optional[str] = None
    categories: List[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


__all__ = ["EkantipurArticle", "RssArticle"]

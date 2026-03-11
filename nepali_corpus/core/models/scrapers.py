from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from pydantic import Field

from .base import BaseEntity


class ScrapeJob(BaseEntity):
    """Parameters for a scheduled scrape job.

    Inherits from:
        BaseEntity – common model config
    """

    source: str
    params: Dict[str, str] = Field(default_factory=dict)
    scheduled_at: Optional[datetime] = None


class ScrapeResult(BaseEntity):
    """Outcome of a completed scrape run.

    Inherits from:
        BaseEntity – common model config
    """

    source: str
    items: int
    errors: int = 0
    output_path: Optional[str] = None
    finished_at: datetime = Field(default_factory=datetime.utcnow)

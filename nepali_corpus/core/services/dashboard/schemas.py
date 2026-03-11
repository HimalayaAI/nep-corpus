from __future__ import annotations

from typing import Dict

from pydantic import BaseModel


class StatsResponse(BaseModel):
    total_documents: int
    by_source: Dict[str, int]
    by_language: Dict[str, int]

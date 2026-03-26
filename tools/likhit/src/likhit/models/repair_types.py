"""Lightweight types for the generic PDF-to-Markdown conversion path."""

from __future__ import annotations

from dataclasses import dataclass

from likhit.models.types import Table


@dataclass(slots=True)
class RepairedBlock:
    """A lightweight ordered block for Markdown assembly."""

    text: str
    order_index: int
    page_number: int
    heading_level: int | None = None
    list_marker: str | None = None
    table: Table | None = None

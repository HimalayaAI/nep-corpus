"""Shared extraction abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from likhit.models import Table


@dataclass(slots=True)
class TextFragment:
    """A normalized text block plus its page/layout metadata."""

    text: str
    page_number: int
    x0: float
    y0: float
    x1: float
    y1: float
    block_number: int = 0
    line_number: int = 0
    gap_before: float | None = None


@dataclass(slots=True)
class RawDocument:
    """Unstructured text extracted from a document."""

    paragraphs: list[str]
    raw_text: str
    fragments: list[TextFragment]
    tables: list[Table] = field(default_factory=list)


class ExtractionStrategy(ABC):
    """Base interface for extraction strategies."""

    @abstractmethod
    def extract_text(self, file_path: str, pages: str | None = None) -> RawDocument:
        """Extract unstructured text from a document."""

    @abstractmethod
    def extract_tables(self, file_path: str) -> list[Table]:
        """Extract structured tables from a document."""

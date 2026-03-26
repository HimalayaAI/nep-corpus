"""Core data models for the extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from likhit.errors import ValidationError
from likhit.version import __version__


class DocumentType(str, Enum):
    """Supported document types."""

    CIAA_PRESS_RELEASE = "ciaa-press-release"
    KANUN_PATRIKA = "kanun-patrika"

    @classmethod
    def parse(cls, value: str) -> "DocumentType":
        try:
            return cls(value)
        except ValueError as exc:
            supported = ", ".join(item.value for item in cls)
            raise ValidationError(
                f"Unsupported document type '{value}'. Supported types: {supported}"
            ) from exc


@dataclass(slots=True)
class Section:
    """A logical document section."""

    heading: str | None
    body: str
    level: int = 1
    subsections: list["Section"] = field(default_factory=list)
    blocks: list["ContentBlock"] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.body, str):
            raise ValidationError("Section body must be a string")
        if self.level < 1 or self.level > 6:
            raise ValidationError("Section level must be between 1 and 6")
        self.body = self.body.strip()
        if not self.body:
            raise ValidationError("Section body cannot be empty")
        if self.heading is not None:
            if not isinstance(self.heading, str):
                raise ValidationError("Section heading must be a string or None")
            self.heading = self.heading.strip() or None


@dataclass(slots=True)
class TableRegion:
    """A per-page bounding box belonging to a table."""

    page_number: int
    x0: float
    y0: float
    x1: float
    y1: float
    page_height: float = 0.0


@dataclass(slots=True)
class TableCell:
    """A single table cell anchored at its top-left position."""

    row: int
    col: int
    text: str
    rowspan: int = 1
    colspan: int = 1


@dataclass(slots=True)
class Table:
    """Extracted table data."""

    row_count: int
    col_count: int
    cells: list[TableCell]
    caption: str | None = None
    index: int = 0
    regions: list[TableRegion] = field(default_factory=list)

    @property
    def page_number(self) -> int:
        return self.regions[0].page_number if self.regions else 1


@dataclass(slots=True)
class ParagraphBlock:
    """A paragraph content block."""

    text: str


@dataclass(slots=True)
class TableBlock:
    """A table content block."""

    table: Table


ContentBlock = ParagraphBlock | TableBlock


@dataclass(slots=True)
class ExtractionResult:
    """Structured result for a converted document."""

    title: str
    doc_type: DocumentType
    source_url: str | None = None
    publication_date: str | None = None
    likhit_version: str = __version__
    sections: list[Section] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.title, str):
            raise ValidationError("ExtractionResult title must be a string")
        self.title = self.title.strip()
        if not self.title:
            raise ValidationError("ExtractionResult title cannot be empty")
        if not isinstance(self.doc_type, DocumentType):
            raise ValidationError("doc_type must be a DocumentType")
        if not self.sections:
            raise ValidationError("ExtractionResult requires at least one section")

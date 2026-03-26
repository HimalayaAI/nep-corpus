"""Document type handler abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod

from likhit.extractors.base import ExtractionStrategy, RawDocument
from likhit.models import ExtractionResult


class DocumentTypeHandler(ABC):
    """Coordinates extraction and document-specific normalization."""

    @abstractmethod
    def get_extraction_strategy(self) -> ExtractionStrategy:
        """Return the strategy used for the document type."""

    def get_extraction_strategy_for_file(self, file_path: str) -> ExtractionStrategy:
        """Return the appropriate strategy based on file extension.

        Override this method to support multiple file formats (PDF, DOCX, DOC).
        Default implementation returns get_extraction_strategy().
        """
        return self.get_extraction_strategy()

    @abstractmethod
    def build_result(
        self, raw_document: RawDocument, metadata: dict[str, str | None]
    ) -> ExtractionResult:
        """Build a structured result from extracted text."""

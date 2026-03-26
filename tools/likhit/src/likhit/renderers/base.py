"""Renderer abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod

from likhit.models import ExtractionResult


class OutputRenderer(ABC):
    """Base interface for output renderers."""

    @abstractmethod
    def render(self, result: ExtractionResult) -> str:
        """Render a structured result to a serialized format."""

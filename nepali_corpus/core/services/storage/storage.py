"""Storage service abstractions for Nepali Corpus."""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict


class StorageService(BaseModel, ABC):
    """Abstract base class for storage services."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the storage backend."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the storage backend."""
        ...

    @abstractmethod
    def create_session(self) -> "StorageSession":
        """Create a scoped storage session."""
        ...


class StorageSession(BaseModel, ABC):
    """Abstract base class for storage sessions."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @abstractmethod
    async def store_training_document(self, doc) -> str:
        """Store a single training document."""
        ...

    @abstractmethod
    async def store_training_documents(self, docs) -> int:
        """Store multiple training documents."""
        ...

    @abstractmethod
    async def list_recent_documents(self, limit: int = 50):
        """List recent documents."""
        ...

    @abstractmethod
    async def get_stats(self) -> dict:
        """Aggregate corpus statistics."""
        ...

    @abstractmethod
    async def seen_url(self, url: str) -> bool:
        """Return True if URL has been seen before."""
        ...

    @abstractmethod
    async def mark_url(self, url: str) -> None:
        """Mark URL as seen."""
        ...

    @abstractmethod
    async def count_urls(self) -> int:
        """Return count of seen URLs."""
        ...

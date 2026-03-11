from __future__ import annotations

from .base import BaseEntity


class StorageConfig(BaseEntity):
    """Minimal configuration for a storage backend.

    Inherits from:
        BaseEntity – common model config
    """

    database_url: str

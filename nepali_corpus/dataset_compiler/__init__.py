"""Shared adapters for compiling packaged datasets into Hugging Face shards."""

from .adapters import (
    AdapterContext,
    MappedItem,
    ModalityAdapter,
    get_adapter,
    infer_adapter_name,
)

__all__ = [
    "AdapterContext",
    "MappedItem",
    "ModalityAdapter",
    "get_adapter",
    "infer_adapter_name",
]

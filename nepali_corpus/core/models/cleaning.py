from __future__ import annotations

from .base import BaseEntity


class CleaningConfig(BaseEntity):
    """Configuration for the text cleaning step.

    Inherits from:
        BaseEntity – common model config
    """

    min_chars: int = 200
    nepali_ratio: float = 0.4


class CleaningResult(BaseEntity):
    """Summary of a cleaning pass.

    Inherits from:
        BaseEntity – common model config
    """

    input_count: int
    output_count: int
    filtered_short: int = 0
    filtered_language: int = 0

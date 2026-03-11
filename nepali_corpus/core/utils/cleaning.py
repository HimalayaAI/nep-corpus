from __future__ import annotations

from typing import Optional

from .normalize import detect_nepali, normalize_text
from ..models import NormalizedDocument


def clean_text(text: str) -> str:
    return normalize_text(text)


def is_nepali(doc: NormalizedDocument, min_ratio: float = 0.4) -> bool:
    if doc.language == "ne":
        return True
    return detect_nepali(doc.text, min_ratio=min_ratio)


def min_length(doc: NormalizedDocument, min_chars: int = 200) -> bool:
    return len(doc.text) >= min_chars

from __future__ import annotations

from typing import Optional

from .normalize import detect_nepali, normalize_text, _HAS_RUST, _rust
from ..models import NormalizedDocument


def clean_text(text: str) -> str:
    if _HAS_RUST:
        return _rust.clean_content(text, 0)
    return normalize_text(text)


def is_nepali(doc: NormalizedDocument, min_ratio: float = 0.4) -> bool:
    if doc.language == "ne":
        return True
    if _HAS_RUST:
        lang = _rust.detect_language(doc.text)
        if lang:
            return lang == "ne"
    return detect_nepali(doc.text, min_ratio=min_ratio)


def min_length(doc: NormalizedDocument, min_chars: int = 200) -> bool:
    return len(doc.text) >= min_chars

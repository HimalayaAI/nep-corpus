from __future__ import annotations

from typing import Optional

from .normalize import detect_nepali, normalize_text
from ..models import NormalizedDocument


def clean_text(text: str) -> str:
    try:
        from rust_url_dedup import clean_content
        return clean_content(text, 0)
    except ImportError:
        pass
    return normalize_text(text)


def is_nepali(doc: NormalizedDocument, min_ratio: float = 0.4) -> bool:
    if doc.language == "ne":
        return True
    try:
        from rust_url_dedup import detect_language
        lang = detect_language(doc.text)
        if lang:
            return lang == "ne"
    except ImportError:
        pass
    return detect_nepali(doc.text, min_ratio=min_ratio)


def min_length(doc: NormalizedDocument, min_chars: int = 200) -> bool:
    return len(doc.text) >= min_chars

"""Backward-compatible imports for the shared dataset compiler filters."""

from nepali_corpus.dataset_compiler.quality_filters import (
    FilterSpec,
    ShortTextPolicy,
    compute_metrics,
    devanagari_ratio,
    digit_ratio,
    has_sentence_punct,
    max_repeated_char_ratio,
    normalize_text,
    passes_quality,
    symbol_ratio,
    word_count,
)

__all__ = [
    "FilterSpec",
    "ShortTextPolicy",
    "compute_metrics",
    "devanagari_ratio",
    "digit_ratio",
    "has_sentence_punct",
    "max_repeated_char_ratio",
    "normalize_text",
    "passes_quality",
    "symbol_ratio",
    "word_count",
]

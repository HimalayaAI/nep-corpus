from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\S+")
_SENTENCE_PUNCT = "।?!"
_DEVANAGARI_RANGES = (
    (0x0900, 0x097F),  # Devanagari
    (0xA8E0, 0xA8FF),  # Devanagari Extended
    (0x1CD0, 0x1CFF),  # Vedic Extensions
)


def normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def _is_devanagari(ch: str) -> bool:
    cp = ord(ch)
    for start, end in _DEVANAGARI_RANGES:
        if start <= cp <= end:
            return True
    return False


def devanagari_ratio(text: str) -> float:
    total = 0
    dev = 0
    for ch in text:
        if ch.isalpha() or ch.isdigit():
            total += 1
            if _is_devanagari(ch):
                dev += 1
    if total == 0:
        return 0.0
    return dev / total


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def has_sentence_punct(text: str) -> bool:
    return any(ch in _SENTENCE_PUNCT for ch in text)


def digit_ratio(text: str) -> float:
    digits = 0
    alnum = 0
    for ch in text:
        if ch.isalnum():
            alnum += 1
            if ch.isdigit():
                digits += 1
    if alnum == 0:
        return 0.0
    return digits / alnum


def symbol_ratio(text: str) -> float:
    symbols = 0
    non_space = 0
    for ch in text:
        if not ch.isspace():
            non_space += 1
            if not ch.isalnum():
                symbols += 1
    if non_space == 0:
        return 0.0
    return symbols / non_space


def max_repeated_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    max_run = 1
    current = 1
    for i in range(1, len(text)):
        if text[i] == text[i - 1]:
            current += 1
            if current > max_run:
                max_run = current
        else:
            current = 1
    return max_run / len(text)


def compute_metrics(text_norm: str) -> Dict[str, float]:
    return {
        "length": len(text_norm),
        "word_count": word_count(text_norm),
        "devanagari_ratio": devanagari_ratio(text_norm),
        "digit_ratio": digit_ratio(text_norm),
        "symbol_ratio": symbol_ratio(text_norm),
        "max_repeated_char_ratio": max_repeated_char_ratio(text_norm),
        "has_sentence_punct": 1.0 if has_sentence_punct(text_norm) else 0.0,
    }


@dataclass(frozen=True)
class ShortTextPolicy:
    max_chars: int = 80
    min_words: int = 6
    require_sentence_punct: bool = True

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "ShortTextPolicy":
        return ShortTextPolicy(
            max_chars=int(raw.get("max_chars", 80)),
            min_words=int(raw.get("min_words", 6)),
            require_sentence_punct=bool(raw.get("require_sentence_punct", True)),
        )

    def merge(self, override: Dict[str, Any]) -> "ShortTextPolicy":
        return ShortTextPolicy(
            max_chars=int(override.get("max_chars", self.max_chars)),
            min_words=int(override.get("min_words", self.min_words)),
            require_sentence_punct=bool(
                override.get("require_sentence_punct", self.require_sentence_punct)
            ),
        )


@dataclass(frozen=True)
class FilterSpec:
    min_chars: int = 0
    min_words: int = 0
    min_devanagari_ratio: float = 0.0
    short_text: Optional[ShortTextPolicy] = None
    max_digit_ratio: Optional[float] = None
    max_symbol_ratio: Optional[float] = None
    max_repeated_char_ratio: Optional[float] = None

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "FilterSpec":
        short_text_raw = raw.get("short_text")
        if isinstance(short_text_raw, dict):
            short_text = ShortTextPolicy.from_dict(short_text_raw)
        elif short_text_raw:
            short_text = ShortTextPolicy()
        else:
            short_text = None

        return FilterSpec(
            min_chars=int(raw.get("min_chars", 0)),
            min_words=int(raw.get("min_words", 0)),
            min_devanagari_ratio=float(raw.get("min_devanagari_ratio", 0.0)),
            short_text=short_text,
            max_digit_ratio=raw.get("max_digit_ratio"),
            max_symbol_ratio=raw.get("max_symbol_ratio"),
            max_repeated_char_ratio=raw.get("max_repeated_char_ratio"),
        )

    def merge(self, override: Optional[Dict[str, Any]]) -> "FilterSpec":
        if not override:
            return self

        short_text = self.short_text
        if "short_text" in override:
            short_override = override.get("short_text")
            if not short_override:
                short_text = None
            elif short_text is None:
                short_text = ShortTextPolicy.from_dict(short_override)
            else:
                short_text = short_text.merge(short_override)

        return FilterSpec(
            min_chars=int(override.get("min_chars", self.min_chars)),
            min_words=int(override.get("min_words", self.min_words)),
            min_devanagari_ratio=float(
                override.get("min_devanagari_ratio", self.min_devanagari_ratio)
            ),
            short_text=short_text,
            max_digit_ratio=override.get("max_digit_ratio", self.max_digit_ratio),
            max_symbol_ratio=override.get("max_symbol_ratio", self.max_symbol_ratio),
            max_repeated_char_ratio=override.get(
                "max_repeated_char_ratio", self.max_repeated_char_ratio
            ),
        )


def passes_quality(text_norm: str, spec: Optional[FilterSpec]) -> bool:
    if spec is None:
        return True

    length = len(text_norm)
    if length < spec.min_chars:
        return False

    words = word_count(text_norm)
    if words < spec.min_words:
        return False

    if spec.min_devanagari_ratio > 0.0 and devanagari_ratio(text_norm) < spec.min_devanagari_ratio:
        return False

    if spec.max_digit_ratio is not None and digit_ratio(text_norm) > spec.max_digit_ratio:
        return False

    if spec.max_symbol_ratio is not None and symbol_ratio(text_norm) > spec.max_symbol_ratio:
        return False

    if spec.max_repeated_char_ratio is not None and max_repeated_char_ratio(text_norm) > spec.max_repeated_char_ratio:
        return False

    if spec.short_text and length < spec.short_text.max_chars:
        if words < spec.short_text.min_words:
            return False
        if spec.short_text.require_sentence_punct and not has_sentence_punct(text_norm):
            return False

    return True

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Optional

from ..models import NormalizedDocument, RawRecord


_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\u200b", "", text)  # zero-width space
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def devanagari_ratio(text: str) -> float:
    if not text:
        return 0.0
    total = len(text)
    if total == 0:
        return 0.0
    matches = len(_DEVANAGARI_RE.findall(text))
    return matches / total


def detect_nepali(text: str, min_ratio: float = 0.4) -> bool:
    return devanagari_ratio(text) >= min_ratio


def make_doc_id(source_id: str, url: str) -> str:
    raw = f"{source_id}:{url}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def make_dedup_key(text: str) -> str:
    norm = normalize_text(text).lower()
    norm = re.sub(r"[^\w\s]", " ", norm)
    norm = _WHITESPACE_RE.sub(" ", norm).strip()
    return hashlib.md5(norm.encode("utf-8")).hexdigest()


def pick_best_text(record: RawRecord, enriched_text: Optional[str] = None) -> str:
    for candidate in [enriched_text, record.content, record.summary, record.title]:
        if candidate and candidate.strip():
            return candidate
    return ""


def normalize_record(
    record: RawRecord,
    enriched_text: Optional[str] = None,
    default_language: str = "ne",
) -> Optional[NormalizedDocument]:
    text = pick_best_text(record, enriched_text)
    
    try:
        from rust_url_dedup import clean_content
        text = clean_content(text, 0)
    except ImportError:
        text = normalize_text(text)
        
    if not text:
        return None

    # Smarter language detection based on character analysis
    try:
        from rust_url_dedup import detect_language
        lang = detect_language(text)
        if lang:
            language = lang
        else:
            ratio = devanagari_ratio(text)
            language = "ne" if ratio >= 0.15 else "en"
    except ImportError:
        ratio = devanagari_ratio(text)
        language = "ne" if ratio >= 0.15 else "en"

    doc = NormalizedDocument(
        id=make_doc_id(record.source_id, record.url),
        url=record.url,
        text=text,
        language=language,
        source_id=record.source_id,
        source_name=record.source_name,
        published_at=record.published_at,
        date_bs=record.date_bs,
        category=record.category,
        province=record.province,
        district=record.district,
        tags=record.tags,
        dedup_key=make_dedup_key(text),
        raw_meta=record.raw_meta,
    )
    return doc

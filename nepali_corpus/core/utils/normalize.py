from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import List, Optional, Tuple

from ..models import NormalizedDocument, RawRecord

try:
    import rust_url_dedup as _rust
    _HAS_RUST = True
except ImportError:
    _rust = None
    _HAS_RUST = False

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
    alpha = sum(1 for c in text if c.isalpha())
    if alpha == 0:
        return 0.0
    matches = len(_DEVANAGARI_RE.findall(text))
    return matches / alpha


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

    if _HAS_RUST:
        text = _rust.clean_content(text, 0)
    else:
        text = normalize_text(text)

    if not text:
        return None

    if _HAS_RUST:
        lang = _rust.detect_language(text)
        language = lang if lang else ("ne" if devanagari_ratio(text) >= 0.15 else "en")
    else:
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


def batch_normalize_records(
    pairs: List[Tuple[RawRecord, Optional[str]]],
    min_chars: int = 200,
    min_devanagari: float = 0.4,
) -> List[NormalizedDocument]:
    if not pairs:
        return []

    records = [p[0] for p in pairs]
    raw_texts = [pick_best_text(rec, ext) for rec, ext in pairs]

    if _HAS_RUST:
        normed = _rust.batch_normalize(raw_texts)
        dedup_keys = _rust.batch_dedup_keys(normed)
        ratios = _rust.batch_devanagari_ratio(normed)
    else:
        normed = [normalize_text(t) for t in raw_texts]
        dedup_keys = [make_dedup_key(t) for t in normed]
        ratios = [devanagari_ratio(t) for t in normed]

    results: List[NormalizedDocument] = []
    for rec, text, dedup_key, ratio in zip(records, normed, dedup_keys, ratios):
        if not text or len(text) < min_chars:
            continue
        if ratio < min_devanagari:
            continue
        language = "ne" if ratio >= 0.15 else "en"
        results.append(NormalizedDocument(
            id=make_doc_id(rec.source_id, rec.url),
            url=rec.url,
            text=text,
            language=language,
            source_id=rec.source_id,
            source_name=rec.source_name,
            published_at=rec.published_at,
            date_bs=rec.date_bs,
            category=rec.category,
            province=rec.province,
            district=rec.district,
            tags=rec.tags,
            dedup_key=dedup_key,
            raw_meta=rec.raw_meta,
        ))

    return results

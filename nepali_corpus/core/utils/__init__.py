"""Utility pipeline helpers (cleaning, dedup, enrichment, export, normalize)."""

from .cleaning import clean_text, is_nepali, min_length
from .dedup import deduplicate
from .enrichment import extract_text, fetch_content
from .export import export_jsonl
from .normalize import (
    detect_nepali,
    make_dedup_key,
    make_doc_id,
    normalize_record,
    normalize_text,
)
from .io import ensure_parent_dir, maybe_gzip_path, open_text
from .writer import JsonlWriter

__all__ = [
    "clean_text",
    "is_nepali",
    "min_length",
    "deduplicate",
    "extract_text",
    "fetch_html",
    "export_jsonl",
    "detect_nepali",
    "make_dedup_key",
    "make_doc_id",
    "normalize_record",
    "normalize_text",
    "ensure_parent_dir",
    "maybe_gzip_path",
    "open_text",
    "JsonlWriter",
]

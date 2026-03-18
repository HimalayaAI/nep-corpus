from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, List, Optional, Tuple

from ..core.models import NormalizedDocument, RawRecord, TrainingDocument
from ..core.utils.normalize import normalize_record
from ..core.services.scrapers import (
    dao_scraper,
    ekantipur_scraper,
    govt_scraper,
    news_rss_scraper,
    social_scraper,
)
from ..core.services.scrapers.registry import load_registry
from ..core.services.storage.env_storage import EnvStorageService
from ..core.utils.cleaning import clean_text, is_nepali, min_length
from ..core.utils.dedup import deduplicate
from ..core.utils.enrichment import extract_text, fetch_content
from ..core.utils.export import export_jsonl
from ..core.utils.io import ensure_parent_dir, maybe_gzip_path, open_text

def ingest_sources_iter(
    rss: bool = True,
    ekantipur: bool = True,
    govt: bool = True,
    dao: bool = True,
    sources: Optional[List[str]] = None,
    govt_registry_path: Optional[str] = None,
    govt_registry_groups: Optional[List[str]] = None,
    govt_pages: int = 3,
    social: bool = True,
) -> Iterable[RawRecord]:
    """
    Iterator version of ingest_sources.
    """
    if sources is not None:
        normalized: set[str] = set()
        for src in sources:
            key = (src or "").strip().lower()
            if not key:
                continue
            if key in ("all", "*"):
                normalized.update(["rss", "ekantipur", "govt", "dao", "social"])
                continue
            if key in ("news", "rss"):
                normalized.add("rss")
            elif key in ("ekantipur",):
                normalized.add("ekantipur")
            elif key in ("govt", "government"):
                normalized.add("govt")
            elif key in ("dao", "district"):
                normalized.add("dao")
            elif key in ("social", "twitter", "nitter"):
                normalized.add("social")
        if normalized:
            rss = "rss" in normalized
            ekantipur = "ekantipur" in normalized
            govt = "govt" in normalized
            # Include DAO by default only when no govt registry groups are specified.
            include_dao_default = not (govt_registry_groups and len(govt_registry_groups) > 0)
            dao = "dao" in normalized or (govt and include_dao_default)
            social = "social" in normalized

    if rss:
        for rec in news_rss_scraper.fetch_raw_records():
            yield rec
    if ekantipur:
        for rec in ekantipur_scraper.fetch_raw_records():
            yield rec
    if govt:
        registry_entries = None
        if govt_registry_path:
            registry_entries = load_registry(govt_registry_path, groups=govt_registry_groups)
        allow_default = govt_registry_path is None
        for rec in govt_scraper.fetch_registry_records(
            registry_entries,
            pages=govt_pages,
            allow_default=allow_default,
        ):
            yield rec
    if dao:
        for rec in dao_scraper.fetch_raw_records():
            yield rec
    if social:
        for rec in social_scraper.fetch_raw_records(max_pages=govt_pages):
            yield rec

def ingest_sources(
    rss: bool = True,
    ekantipur: bool = True,
    govt: bool = True,
    dao: bool = True,
    social: bool = True,
    sources: Optional[List[str]] = None,
    govt_registry_path: Optional[str] = None,
    govt_registry_groups: Optional[List[str]] = None,
    govt_pages: int = 3,
) -> List[RawRecord]:
    return list(
        ingest_sources_iter(
            rss=rss,
            ekantipur=ekantipur,
            govt=govt,
            dao=dao,
            social=social,
            sources=sources,
            govt_registry_path=govt_registry_path,
            govt_registry_groups=govt_registry_groups,
            govt_pages=govt_pages,
        )
    )


def save_raw_jsonl(records: Iterable[RawRecord], path: str, gzip_output: bool = False) -> int:
    path = maybe_gzip_path(path, gzip_output)
    ensure_parent_dir(path)
    count = 0
    with open_text(path, "wt") as f:
        for rec in records:
            f.write(json.dumps(rec.model_dump(), ensure_ascii=False) + "\n")
            count += 1
    return count


def load_raw_jsonl(path: str) -> List[RawRecord]:
    records: List[RawRecord] = []
    with open_text(path, "rt") as f:
        for line in f:
            if not line.strip():
                continue
            records.append(RawRecord(**json.loads(line)))
    return records


def save_normalized_jsonl(
    docs: Iterable[NormalizedDocument],
    path: str,
    gzip_output: bool = False,
) -> int:
    path = maybe_gzip_path(path, gzip_output)
    ensure_parent_dir(path)
    count = 0
    with open_text(path, "wt") as f:
        for doc in docs:
            f.write(json.dumps(doc.model_dump(), ensure_ascii=False) + "\n")
            count += 1
    return count


def load_normalized_jsonl(path: str) -> List[NormalizedDocument]:
    docs: List[NormalizedDocument] = []
    with open_text(path, "rt") as f:
        for line in f:
            if not line.strip():
                continue
            docs.append(NormalizedDocument(**json.loads(line)))
    return docs


logger = logging.getLogger(__name__)


def enrich_records(
    records: Iterable[RawRecord],
    cache_dir: str,
    min_enrich_len: int = 1000,
    max_workers: int = 10,
    ocr_enabled: bool = True,
    pdf_enabled: bool = True,
) -> List[Tuple[RawRecord, Optional[str]]]:
    """Parallel enrichment using ThreadPoolExecutor."""
    records_list = list(records)
    enriched: List[Tuple[RawRecord, Optional[str]]] = [None] * len(records_list)
    total = len(records_list)
    
    logger.info(f"Enriching {total} records with {max_workers} workers...")

    def _enrich_one(index: int, rec: RawRecord):
        text = rec.content or rec.summary or ""
        if len(text) >= min_enrich_len:
            enriched[index] = (rec, None)
            return

        try:
            data, content_type = fetch_content(rec.url, cache_dir=cache_dir)
            extracted = extract_text(
                data,
                content_type=content_type,
                url=rec.url,
                ocr_enabled=ocr_enabled,
                pdf_enabled=pdf_enabled,
            ) if data else None
            enriched[index] = (rec, extracted)
        except Exception as e:
            logger.warning(f"Error enriching {rec.url}: {e}")
            enriched[index] = (rec, None)

        # Basic progress logging
        done = sum(1 for x in enriched if x is not None)
        if done % 10 == 0 or done == total:
            logger.info(f"Enrichment progress: {done}/{total} ({(done/total)*100:.1f}%)")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for i, rec in enumerate(records_list):
            executor.submit(_enrich_one, i, rec)

    return enriched


def normalize_and_filter(
    enriched_records: Iterable[Tuple[RawRecord, Optional[str]]],
    min_chars: int = 200,
    nepali_ratio: float = 0.4,
) -> List[NormalizedDocument]:
    docs: List[NormalizedDocument] = []
    for rec, extracted in enriched_records:
        doc = normalize_record(rec, enriched_text=extracted)
        if not doc:
            continue
        doc.text = clean_text(doc.text)
        if not min_length(doc, min_chars=min_chars):
            continue
        if not is_nepali(doc, min_ratio=nepali_ratio):
            continue
        docs.append(doc)
    return docs


def to_training_docs(docs: Iterable[NormalizedDocument]) -> List[TrainingDocument]:
    training: List[TrainingDocument] = []
    for d in docs:
        training.append(
            TrainingDocument(
                id=d.id,
                text=d.text,
                source_id=d.source_id,
                source_name=d.source_name,
                url=d.url,
                language=d.language,
                published_at=d.published_at,
                date_bs=d.date_bs,
                category=d.category,
                province=d.province,
                district=d.district,
                tags=d.tags,
            )
        )
    return training


def run_pipeline(
    raw_out: str,
    final_out: str,
    cache_dir: str,
    govt_registry_path: Optional[str] = None,
    govt_registry_groups: Optional[List[str]] = None,
    govt_pages: int = 3,
    gzip_output: bool = False,
) -> int:
    raw_records = ingest_sources(
        govt_registry_path=govt_registry_path,
        govt_registry_groups=govt_registry_groups,
        govt_pages=govt_pages,
    )
    save_raw_jsonl(raw_records, raw_out, gzip_output=gzip_output)

    enriched = enrich_records(raw_records, cache_dir=cache_dir)
    normalized = normalize_and_filter(enriched)
    unique = deduplicate(normalized)
    training = to_training_docs(unique)
    count = export_jsonl(training, final_out, gzip_output=gzip_output)
    
    # Sync to DB
    try:
        import asyncio
        async def _sync():
            storage = EnvStorageService()
            await storage.initialize()
            session = storage.create_session()
            await session.store_training_documents(training)
            await storage.close()
        
        logger.info(f"Syncing {len(training)} documents to database...")
        asyncio.run(_sync())
    except Exception as e:
        logger.warning(f"Failed to sync to database: {e}")

    return count

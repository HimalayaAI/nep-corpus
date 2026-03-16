#!/usr/bin/env python3
"""
Merge multiple Nepali text datasets into a single HF repo with streaming, text-hash
dedupe, and incremental append support.

Standard output schema:
  text (required), source (required), url (optional), language (optional), doc_id (optional)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import yaml
from datasets import Dataset, load_dataset
from huggingface_hub import HfApi, get_token, login


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")
_HF_SHARD_RE = re.compile(r"^data/train-(\d+)")
_DEVANAGARI_RANGES = (
    (0x0900, 0x097F),  # Devanagari
    (0xA8E0, 0xA8FF),  # Devanagari Extended
    (0x1CD0, 0x1CFF),  # Vedic Extensions (rare but safe)
)


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


def normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def hash_text(text: str) -> bytes:
    # 16-byte digest for compact storage
    import hashlib

    return hashlib.blake2b(text.encode("utf-8", errors="ignore"), digest_size=16).digest()


def item_get(item: Any, key: str) -> Any:
    if hasattr(item, "get"):
        try:
            return item.get(key)
        except Exception:
            pass
    try:
        return item[key]
    except Exception:
        return None


def get_field_value(item: Any, field_spec: Any) -> Any:
    if field_spec is None:
        return None
    if isinstance(field_spec, (list, tuple)):
        for spec in field_spec:
            val = get_field_value(item, spec)
            if val is not None:
                return val
        return None
    if isinstance(field_spec, str) and "." in field_spec:
        current = item
        for part in field_spec.split("."):
            if current is None:
                return None
            current = item_get(current, part)
        return current
    if isinstance(field_spec, str):
        return item_get(item, field_spec)
    return None


@dataclass
class SourceConfig:
    name: str
    kind: str
    repo: Optional[str] = None
    split: str = "train"
    path: Optional[str] = None
    fields: Optional[Dict[str, Any]] = None


class DedupeStore:
    def __init__(self, path: str, reset: bool = False) -> None:
        self.path = path
        if reset and os.path.exists(path):
            os.remove(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("CREATE TABLE IF NOT EXISTS text_hashes (hash BLOB PRIMARY KEY);")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def insert_hashes(self, hashes: List[bytes]) -> None:
        if not hashes:
            return
        rows = [(sqlite3.Binary(h),) for h in hashes]
        self.conn.executemany("INSERT OR IGNORE INTO text_hashes(hash) VALUES (?);", rows)
        self.conn.commit()

    def filter_new(self, items: List[Tuple[bytes, Dict[str, Any]]]) -> List[Tuple[bytes, Dict[str, Any]]]:
        if not items:
            return []

        seen: set[bytes] = set()
        unique_items: List[Tuple[bytes, Dict[str, Any]]] = []
        for h, row in items:
            if h in seen:
                continue
            seen.add(h)
            unique_items.append((h, row))

        hashes = [h for h, _ in unique_items]
        existing: set[bytes] = set()

        chunk_size = 900  # keep under SQLite max variable limit
        for i in range(0, len(hashes), chunk_size):
            chunk = hashes[i : i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            cursor = self.conn.execute(
                f"SELECT hash FROM text_hashes WHERE hash IN ({placeholders});",
                chunk,
            )
            existing.update(row[0] for row in cursor.fetchall())

        return [(h, row) for h, row in unique_items if h not in existing]


def iter_hf_dataset(repo: str, split: str = "train") -> Iterator[Dict[str, Any]]:
    dataset = load_dataset(repo, split=split, streaming=True)
    for row in dataset:
        yield row


def iter_jsonl(path: str) -> Iterator[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def iter_parquet(path: str) -> Iterator[Dict[str, Any]]:
    dataset = load_dataset("parquet", data_files=path, split="train", streaming=True)
    for row in dataset:
        yield row


def get_max_shard_index(api: HfApi, repo_id: str) -> int:
    try:
        files = api.list_repo_files(repo_id, repo_type="dataset")
    except Exception:
        return 0
    max_idx = 0
    for path in files:
        m = _HF_SHARD_RE.match(path)
        if not m:
            continue
        idx = int(m.group(1))
        if idx > max_idx:
            max_idx = idx
    return max_idx


def load_config(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_sources(raw_sources: List[Dict[str, Any]]) -> List[SourceConfig]:
    sources: List[SourceConfig] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        kind = raw.get("kind")
        if not name or not kind:
            continue
        sources.append(
            SourceConfig(
                name=name,
                kind=kind,
                repo=raw.get("repo"),
                split=raw.get("split", "train"),
                path=raw.get("path"),
                fields=raw.get("fields") or {},
            )
        )
    return sources


def map_item_to_schema(
    item: Dict[str, Any],
    source_name: str,
    fields: Dict[str, Any],
    min_chars: int,
    filter_nepali: bool,
    min_devanagari_ratio: float,
    default_language: Optional[str],
) -> Optional[Tuple[Dict[str, Any], str]]:
    text_key = fields.get("text", "text")
    text_val = get_field_value(item, text_key)
    if text_val is None:
        return None

    text_str = str(text_val)
    text_norm = normalize_text(text_str)
    if not text_norm or len(text_norm) < min_chars:
        return None
    if filter_nepali and devanagari_ratio(text_norm) < min_devanagari_ratio:
        return None

    row: Dict[str, Any] = {
        "text": text_str.strip(),
        "source": source_name,
    }

    url_key = fields.get("url")
    if url_key:
        url_val = get_field_value(item, url_key)
        if url_val is not None:
            row["url"] = str(url_val)

    language_key = fields.get("language")
    if language_key:
        lang_val = get_field_value(item, language_key)
        if lang_val is not None:
            row["language"] = str(lang_val)
    elif default_language:
        row["language"] = default_language

    doc_id_key = fields.get("doc_id")
    if doc_id_key:
        doc_val = get_field_value(item, doc_id_key)
        if doc_val is not None:
            row["doc_id"] = str(doc_val)

    return row, text_norm


def prefill_dedupe_from_hf(store: DedupeStore, repo_id: str) -> None:
    logger.info("Prefilling dedupe store from existing HF dataset: %s", repo_id)
    count = 0
    buffer: List[bytes] = []
    for row in iter_hf_dataset(repo_id, split="train"):
        text = row.get("text")
        if not text:
            continue
        text_norm = normalize_text(str(text))
        if not text_norm:
            continue
        buffer.append(hash_text(text_norm))
        if len(buffer) >= 5000:
            store.insert_hashes(buffer)
            count += len(buffer)
            buffer = []
            if count % 50000 == 0:
                logger.info("  Prefilled %s hashes...", count)
    if buffer:
        store.insert_hashes(buffer)
        count += len(buffer)
    logger.info("Prefill complete. Total hashes inserted: %s", count)


def upload_parquet_batch(
    *,
    api: HfApi,
    repo_id: str,
    token: str,
    rows: List[Dict[str, Any]],
    shard_index: int,
) -> None:
    data_dict = {
        "text": [row.get("text") for row in rows],
        "source": [row.get("source") for row in rows],
        "url": [row.get("url") for row in rows],
        "language": [row.get("language") for row in rows],
        "doc_id": [row.get("doc_id") for row in rows],
    }

    hf_dataset = Dataset.from_dict(data_dict)
    os.makedirs("data/hf_merge_export", exist_ok=True)
    parquet_path = f"data/hf_merge_export/train-{shard_index:06d}-of-000000.parquet"
    repo_path = f"data/train-{shard_index:06d}-of-000000.parquet"
    hf_dataset.to_parquet(parquet_path)

    api.upload_file(
        path_or_fileobj=parquet_path,
        path_in_repo=repo_path,
        repo_id=repo_id,
        repo_type="dataset",
        token=token,
    )

    os.remove(parquet_path)


def iter_source_items(source: SourceConfig) -> Iterator[Dict[str, Any]]:
    if source.kind == "hf":
        if not source.repo:
            raise ValueError(f"HF source missing repo: {source.name}")
        yield from iter_hf_dataset(source.repo, split=source.split)
    elif source.kind == "jsonl":
        if not source.path:
            raise ValueError(f"JSONL source missing path: {source.name}")
        yield from iter_jsonl(source.path)
    elif source.kind == "parquet":
        if not source.path:
            raise ValueError(f"Parquet source missing path: {source.name}")
        yield from iter_parquet(source.path)
    else:
        raise ValueError(f"Unsupported source kind: {source.kind}")


def merge_and_upload(
    *,
    sources: List[SourceConfig],
    repo_id: str,
    token: str,
    batch_size: int,
    incremental: Optional[bool],
    refresh_dedupe: bool,
    dedupe_store_path: str,
    max_batches: Optional[int],
    min_chars: int,
    filter_nepali: bool,
    min_devanagari_ratio: float,
    default_language: Optional[str],
) -> None:
    api = HfApi()

    repo_exists = True
    try:
        api.repo_info(repo_id, repo_type="dataset")
        logger.info("Repository %s exists.", repo_id)
    except Exception:
        repo_exists = False
        logger.info("Creating repository %s ...", repo_id)
        api.create_repo(repo_id, repo_type="dataset", private=True)

    if incremental is None:
        incremental = repo_exists

    if repo_exists and not incremental:
        logger.warning(
            "Full merge requested on an existing repo; this will append new shards and may duplicate data."
        )

    store = DedupeStore(dedupe_store_path, reset=refresh_dedupe)
    try:
        if repo_exists and incremental and refresh_dedupe:
            prefill_dedupe_from_hf(store, repo_id)

        max_index = get_max_shard_index(api, repo_id) if repo_exists else 0
        shard_index = max_index + 1

        logger.info("Starting shard index: %s", shard_index)

        out_rows: List[Dict[str, Any]] = []
        out_hashes: List[bytes] = []
        out_hash_set: set[bytes] = set()
        pending: List[Tuple[bytes, Dict[str, Any]]] = []
        uploaded_batches = 0

        for source in sources:
            logger.info("Processing source: %s", source.name)
            for item in iter_source_items(source):
                mapped = map_item_to_schema(
                    item,
                    source.name,
                    source.fields or {},
                    min_chars,
                    filter_nepali,
                    min_devanagari_ratio,
                    default_language,
                )
                if not mapped:
                    continue
                row, text_norm = mapped
                text_hash = hash_text(text_norm)
                if not row.get("doc_id"):
                    row["doc_id"] = text_hash.hex()
                pending.append((text_hash, row))

                if len(pending) >= 1000:
                    new_pairs = store.filter_new(pending)
                    pending = []
                    for h, new_row in new_pairs:
                        if h in out_hash_set:
                            continue
                        out_rows.append(new_row)
                        out_hashes.append(h)
                        out_hash_set.add(h)

                        if len(out_rows) >= batch_size:
                            upload_parquet_batch(
                                api=api,
                                repo_id=repo_id,
                                token=token,
                                rows=out_rows[:batch_size],
                                shard_index=shard_index,
                            )
                            store.insert_hashes(out_hashes[:batch_size])
                            shard_index += 1
                            uploaded_batches += 1
                            out_rows = out_rows[batch_size:]
                            out_hashes = out_hashes[batch_size:]
                            out_hash_set = set(out_hashes)

                            if max_batches and uploaded_batches >= max_batches:
                                logger.info("Reached max_batches=%s. Stopping early.", max_batches)
                                return

            if pending:
                new_pairs = store.filter_new(pending)
                pending = []
                for h, new_row in new_pairs:
                    if h in out_hash_set:
                        continue
                    out_rows.append(new_row)
                    out_hashes.append(h)
                    out_hash_set.add(h)

                while len(out_rows) >= batch_size:
                    upload_parquet_batch(
                        api=api,
                        repo_id=repo_id,
                        token=token,
                        rows=out_rows[:batch_size],
                        shard_index=shard_index,
                    )
                    store.insert_hashes(out_hashes[:batch_size])
                    shard_index += 1
                    uploaded_batches += 1
                    out_rows = out_rows[batch_size:]
                    out_hashes = out_hashes[batch_size:]
                    out_hash_set = set(out_hashes)

                    if max_batches and uploaded_batches >= max_batches:
                        logger.info("Reached max_batches=%s. Stopping early.", max_batches)
                        return

        if pending:
            new_pairs = store.filter_new(pending)
            pending = []
            for h, new_row in new_pairs:
                if h in out_hash_set:
                    continue
                out_rows.append(new_row)
                out_hashes.append(h)
                out_hash_set.add(h)

        if out_rows and (not max_batches or uploaded_batches < max_batches):
            upload_parquet_batch(
                api=api,
                repo_id=repo_id,
                token=token,
                rows=out_rows,
                shard_index=shard_index,
            )
            store.insert_hashes(out_hashes)
            uploaded_batches += 1

        logger.info("Merge complete. Uploaded %s shard(s).", uploaded_batches)
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge corpora and upload to HF")
    parser.add_argument("--config", help="Path to YAML merge config")
    parser.add_argument("--target-repo", help="Target HF repo (org/name)")
    parser.add_argument("--batch-size", type=int, help="Rows per shard")
    parser.add_argument("--incremental", action="store_true", default=None, help="Enable incremental mode")
    parser.add_argument("--no-incremental", action="store_false", dest="incremental", default=None)
    parser.add_argument(
        "--dedupe-store",
        default=None,
        help="Path to SQLite dedupe DB (default: data/dedupe_text_hashes.sqlite)",
    )
    parser.add_argument("--refresh-dedupe", action="store_true", default=None, help="Refresh dedupe DB")
    parser.add_argument("--no-refresh-dedupe", action="store_false", dest="refresh_dedupe", default=None)
    parser.add_argument("--max-batches", type=int, help="Max number of shards to upload")
    parser.add_argument("--token", help="HF write token (defaults to cache or HF_TOKEN)")

    args = parser.parse_args()

    config = load_config(args.config)
    target_repo = args.target_repo or config.get("target_repo")
    if not target_repo:
        print("Error: target_repo is required (via --target-repo or config).")
        sys.exit(1)

    raw_sources = config.get("sources") or []
    sources = parse_sources(raw_sources)
    if not sources:
        print("Error: No sources configured. Add `sources:` to the config.")
        sys.exit(1)

    options = config.get("options") or {}
    batch_size = args.batch_size or options.get("batch_size") or 50000
    incremental = args.incremental if args.incremental is not None else options.get("incremental")
    refresh_dedupe = args.refresh_dedupe if args.refresh_dedupe is not None else options.get("refresh_dedupe", True)
    max_batches = args.max_batches or options.get("max_batches")
    min_chars = options.get("min_chars", 1)
    filter_nepali = options.get("filter_nepali", False)
    min_devanagari_ratio = options.get("min_devanagari_ratio", 0.3)
    default_language = options.get("default_language")

    dedupe_store = (
        args.dedupe_store
        or options.get("dedupe_store")
        or "data/dedupe_text_hashes.sqlite"
    )

    token = args.token or os.environ.get("HF_TOKEN") or get_token()
    if not token:
        print("Error: No Hugging Face token found. Use --token or set HF_TOKEN.")
        sys.exit(1)

    login(token=token)

    merge_and_upload(
        sources=sources,
        repo_id=target_repo,
        token=token,
        batch_size=batch_size,
        incremental=incremental,
        refresh_dedupe=refresh_dedupe,
        dedupe_store_path=dedupe_store,
        max_batches=max_batches,
        min_chars=min_chars,
        filter_nepali=filter_nepali,
        min_devanagari_ratio=min_devanagari_ratio,
        default_language=default_language,
    )


if __name__ == "__main__":
    main()

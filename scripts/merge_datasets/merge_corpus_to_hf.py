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
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import yaml
from datasets import Dataset, load_dataset
import datasets
from huggingface_hub import HfApi, get_token, login

# Ensure project root is on sys.path for scripts.* imports
project_root = str(Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from nepali_corpus.dataset_compiler.quality_filters import FilterSpec
from nepali_corpus.dataset_compiler.adapters import (
    AdapterContext,
    ModalityAdapter,
    get_adapter,
    infer_adapter_name,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_HF_SHARD_RE = re.compile(r"^data/train-(\d+)")


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
    config: Optional[str] = None
    split: str = "train"
    path: Optional[str] = None
    fields: Optional[Dict[str, Any]] = None
    filters: Optional[Dict[str, Any]] = None
    adapter: str = "text"
    modality: Optional[str] = None
    task_type: Optional[str] = None
    license: Optional[str] = None
    language: Optional[str] = None
    embed_media: Optional[bool] = None
    max_media_bytes: Optional[int] = None


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


def iter_hf_dataset(
    repo: str,
    split: str = "train",
    config: Optional[str] = None,
    media_columns: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    if config:
        dataset = load_dataset(repo, name=config, split=split, streaming=True)
    else:
        dataset = load_dataset(repo, split=split, streaming=True)
    for column, feature in (media_columns or {}).items():
        try:
            dataset = dataset.cast_column(column, feature)
        except Exception as exc:
            logger.warning(
                "Could not set decode=False for %s:%s column %s: %s",
                repo,
                split,
                column,
                exc,
            )
    for row in dataset:
        yield row


def iter_hf_parquet_rows(
    repo: str,
    columns: List[str],
    token: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    """
    Stream selected columns directly from parquet shards to avoid
    schema-cast errors when some shards have all-null optional columns.
    """
    import fsspec
    import pyarrow.parquet as pq

    api = HfApi()
    try:
        files = api.list_repo_files(repo, repo_type="dataset")
    except Exception:
        return

    parquet_files = [f for f in files if f.startswith("data/") and f.endswith(".parquet")]
    if not parquet_files:
        return

    def shard_key(path: str) -> int:
        m = _HF_SHARD_RE.match(path)
        return int(m.group(1)) if m else 0

    parquet_files.sort(key=shard_key)

    fs = fsspec.filesystem("hf", token=token or get_token())
    for path in parquet_files:
        with fs.open(f"hf://datasets/{repo}/{path}", "rb") as fh:
            pf = pq.ParquetFile(fh)
            available = [
                column for column in columns if column in pf.schema_arrow.names
            ]
            if not available:
                continue
            for batch in pf.iter_batches(columns=available):
                for row in batch.to_pylist():
                    yield row


def get_remote_parquet_columns(
    repo: str,
    token: Optional[str] = None,
) -> Optional[set[str]]:
    """Read the first output shard schema without downloading the full dataset."""
    import fsspec
    import pyarrow.parquet as pq

    api = HfApi()
    try:
        files = api.list_repo_files(repo, repo_type="dataset")
    except Exception:
        return None
    parquet_files = sorted(
        path
        for path in files
        if path.startswith("data/") and path.endswith(".parquet")
    )
    if not parquet_files:
        return None

    fs = fsspec.filesystem("hf", token=token or get_token())
    with fs.open(f"hf://datasets/{repo}/{parquet_files[0]}", "rb") as fh:
        return set(pq.ParquetFile(fh).schema_arrow.names)


def validate_existing_repo_schema(
    repo: str,
    adapter: ModalityAdapter,
    token: Optional[str] = None,
) -> None:
    columns = get_remote_parquet_columns(repo, token=token)
    if columns is None:
        return
    expected = set(adapter.features().keys())
    if columns != expected:
        missing = sorted(expected - columns)
        extra = sorted(columns - expected)
        raise ValueError(
            f"Target dataset {repo} does not match the {adapter.name} schema "
            f"(missing={missing}, extra={extra}). Use a separate target repo."
        )


def iter_hf_parquet_texts(repo: str, token: Optional[str] = None) -> Iterator[str]:
    """Backward-compatible text-only view used by older compilers."""
    for row in iter_hf_parquet_rows(repo, ["text"], token=token):
        value = row.get("text")
        if value:
            yield str(value)


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


def load_checkpoint(path: Optional[str]) -> set[str]:
    if not path or not os.path.exists(path):
        return set()
    done: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                done.add(line)
    return done


def append_checkpoint(path: Optional[str], key: str) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(key + "\n")


def checkpoint_key(source: "SourceConfig") -> str:
    prefix = "" if get_adapter(source.adapter).name == "text" else f"{source.adapter}|"
    if source.kind == "hf":
        return f"{prefix}hf|{source.repo}|{source.config or 'default'}|{source.split}"
    if source.kind == "jsonl":
        return f"{prefix}jsonl|{source.path}"
    if source.kind == "parquet":
        return f"{prefix}parquet|{source.path}"
    return f"{prefix}{source.kind}|{source.name}"


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
        adapter = infer_adapter_name(
            adapter=raw.get("adapter"),
            modality=raw.get("modality"),
            task_type=raw.get("task_type"),
        )
        sources.append(
            SourceConfig(
                name=name,
                kind=kind,
                repo=raw.get("repo"),
                config=raw.get("config"),
                split=raw.get("split", "train"),
                path=raw.get("path"),
                fields=raw.get("fields") or {},
                filters=raw.get("filters") or None,
                adapter=adapter,
                modality=raw.get("modality"),
                task_type=raw.get("task_type"),
                license=raw.get("license"),
                language=raw.get("language"),
                embed_media=raw.get("embed_media"),
                max_media_bytes=raw.get("max_media_bytes"),
            )
        )
    return sources


def load_inventory_sources(
    inventory_path: str,
    include_re: Optional[re.Pattern],
    exclude_re: Optional[re.Pattern],
) -> List[SourceConfig]:
    sources: List[SourceConfig] = []
    with open(inventory_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            repo_id = row.get("repo_id")
            raw_config = row.get("config")
            config = (
                None
                if raw_config in (None, "", "default")
                else str(raw_config)
            )
            split = row.get("split") or "train"
            mapping = row.get("mapping_suggested") or {}
            usable = row.get("usable", False)
            if not repo_id or not usable:
                continue
            try:
                adapter = infer_adapter_name(
                    adapter=row.get("adapter"),
                    modality=row.get("modality"),
                    task_type=row.get("task_type"),
                )
            except ValueError:
                continue
            required_fields = {
                "text": ("text",),
                "asr": ("text", "audio"),
                "ocr": ("text", "image"),
                "sft": ("messages", "conversations", "instruction", "prompt"),
            }[adapter]
            if adapter in {"asr", "ocr"}:
                if not all(mapping.get(field) for field in required_fields):
                    continue
            elif not any(mapping.get(field) for field in required_fields):
                continue

            if include_re and not include_re.search(repo_id):
                continue
            if exclude_re and exclude_re.search(repo_id):
                continue

            source_name = repo_id if config is None else f"{repo_id}:{config}"
            sources.append(
                SourceConfig(
                    name=source_name,
                    kind="hf",
                    repo=repo_id,
                    config=config,
                    split=split,
                    fields={k: v for k, v in mapping.items() if v},
                    filters=row.get("filters") or None,
                    adapter=adapter,
                    modality=row.get("modality"),
                    task_type=row.get("task_type"),
                    license=row.get("license"),
                    language=row.get("language"),
                    embed_media=row.get("embed_media"),
                    max_media_bytes=row.get("max_media_bytes"),
                )
            )
    return sources


def cleanup_hf_cache(repo_id: str) -> None:
    cache_root = os.environ.get("HF_DATASETS_CACHE") or datasets.config.HF_DATASETS_CACHE
    if not cache_root or not os.path.isdir(cache_root):
        return
    safe = repo_id.replace("/", "___")
    for entry in os.listdir(cache_root):
        if entry.startswith(safe):
            try:
                path = os.path.join(cache_root, entry)
                shutil.rmtree(path, ignore_errors=True)
            except Exception:
                continue


def map_item_to_schema(
    item: Dict[str, Any],
    source_name: str,
    fields: Dict[str, Any],
    filter_spec: Optional[FilterSpec],
    default_language: Optional[str],
) -> Optional[Tuple[Dict[str, Any], str]]:
    """Backward-compatible text mapper for callers outside the generic runner."""
    mapped = get_adapter("text").map_item(
        item,
        AdapterContext(
            source_name=source_name,
            fields=fields,
            filter_spec=filter_spec,
            default_language=default_language,
        ),
    )
    if not mapped:
        return None
    return mapped.row, mapped.dedupe_key


def prefill_dedupe_from_hf(
    store: DedupeStore,
    repo_id: str,
    token: Optional[str] = None,
    adapter: Optional[ModalityAdapter] = None,
) -> None:
    logger.info("Prefilling dedupe store from existing HF dataset: %s", repo_id)
    adapter = adapter or get_adapter("text")
    count = 0
    buffer: List[bytes] = []
    for row in iter_hf_parquet_rows(
        repo_id,
        adapter.remote_dedupe_columns(),
        token=token,
    ):
        dedupe_key = adapter.remote_dedupe_key(row)
        if not dedupe_key:
            continue
        buffer.append(hash_text(dedupe_key))
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
    adapter: Optional[ModalityAdapter] = None,
) -> None:
    adapter = adapter or get_adapter("text")
    data_dict = adapter.data_dict(rows)
    features = adapter.features()
    hf_dataset = Dataset.from_dict(data_dict, features=features)
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
        adapter = get_adapter(source.adapter)
        yield from iter_hf_dataset(
            source.repo,
            split=source.split,
            config=source.config,
            media_columns=adapter.media_columns(source.fields or {}),
        )
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


def resolve_run_adapter(sources: List[SourceConfig]) -> ModalityAdapter:
    """Require a single output schema per compiler run/Hugging Face dataset."""
    adapter_names = {get_adapter(source.adapter).name for source in sources}
    if len(adapter_names) != 1:
        raise ValueError(
            "A compiler run can emit only one modality schema; split these adapters "
            f"into separate target datasets: {sorted(adapter_names)}"
        )
    return get_adapter(next(iter(adapter_names)))


def build_legacy_filter_spec(options: Dict[str, Any]) -> Optional[FilterSpec]:
    min_chars = options.get("min_chars", 1)
    filter_nepali = options.get("filter_nepali", False)
    min_devanagari_ratio = options.get("min_devanagari_ratio", 0.3)

    if min_chars is None:
        min_chars = 0

    return FilterSpec(
        min_chars=int(min_chars),
        min_words=0,
        min_devanagari_ratio=float(min_devanagari_ratio) if filter_nepali else 0.0,
    )


def resolve_filter_spec(
    *,
    source: SourceConfig,
    global_spec: Optional[FilterSpec],
    legacy_spec: Optional[FilterSpec],
) -> Optional[FilterSpec]:
    spec = global_spec if global_spec is not None else legacy_spec
    if source.filters:
        spec = spec.merge(source.filters) if spec else FilterSpec.from_dict(source.filters)
    return spec


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
    global_filter_spec: Optional[FilterSpec],
    legacy_filter_spec: Optional[FilterSpec],
    default_language: Optional[str],
    cleanup_cache: bool,
    checkpoint_path: Optional[str],
    embed_media: bool,
    max_media_bytes: Optional[int],
    max_batch_bytes: Optional[int],
) -> None:
    adapter = resolve_run_adapter(sources)
    logger.info("Using %s output adapter", adapter.name)
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
    if repo_exists:
        validate_existing_repo_schema(repo_id, adapter, token=token)

    store = DedupeStore(dedupe_store_path, reset=refresh_dedupe)
    try:
        if repo_exists and incremental and refresh_dedupe:
            prefill_dedupe_from_hf(store, repo_id, token=token, adapter=adapter)

        max_index = get_max_shard_index(api, repo_id) if repo_exists else 0
        shard_index = max_index + 1

        logger.info("Starting shard index: %s", shard_index)

        out_rows: List[Dict[str, Any]] = []
        out_hashes: List[bytes] = []
        out_hash_set: set[bytes] = set()
        out_payload_bytes = 0
        pending: List[Tuple[bytes, Dict[str, Any]]] = []
        uploaded_batches = 0
        pending_limit = 1000 if adapter.name in {"text", "sft"} else 100
        done = load_checkpoint(checkpoint_path)
        completed_source_keys: List[str] = []

        def checkpoint_completed_sources() -> None:
            """Checkpoint only after every buffered row has been uploaded."""
            if out_rows:
                return
            while completed_source_keys:
                completed_key = completed_source_keys.pop(0)
                append_checkpoint(checkpoint_path, completed_key)
                done.add(completed_key)

        def add_new_pairs(
            pairs: List[Tuple[bytes, Dict[str, Any]]],
        ) -> None:
            nonlocal out_payload_bytes
            for item_hash, new_row in pairs:
                if item_hash in out_hash_set:
                    continue
                out_rows.append(new_row)
                out_hashes.append(item_hash)
                out_hash_set.add(item_hash)
                out_payload_bytes += adapter.payload_bytes(new_row)

        def flush_output(force: bool = False) -> bool:
            """Upload ready rows; return True once max_batches is reached."""
            nonlocal shard_index, uploaded_batches
            nonlocal out_rows, out_hashes, out_hash_set, out_payload_bytes

            def is_ready() -> bool:
                return bool(out_rows) and (
                    force
                    or len(out_rows) >= batch_size
                    or bool(max_batch_bytes and out_payload_bytes >= max_batch_bytes)
                )

            while is_ready():
                if max_batches and uploaded_batches >= max_batches:
                    return True

                take_count = min(len(out_rows), batch_size)
                if max_batch_bytes:
                    payload = 0
                    take_count = 0
                    for candidate in out_rows[:batch_size]:
                        candidate_bytes = adapter.payload_bytes(candidate)
                        if take_count and payload + candidate_bytes > max_batch_bytes:
                            break
                        take_count += 1
                        payload += candidate_bytes
                        if payload >= max_batch_bytes:
                            break
                take_count = max(1, take_count)

                upload_parquet_batch(
                    api=api,
                    repo_id=repo_id,
                    token=token,
                    rows=out_rows[:take_count],
                    shard_index=shard_index,
                    adapter=adapter,
                )
                store.insert_hashes(out_hashes[:take_count])
                shard_index += 1
                uploaded_batches += 1
                out_rows = out_rows[take_count:]
                out_hashes = out_hashes[take_count:]
                out_hash_set = set(out_hashes)
                out_payload_bytes = sum(
                    adapter.payload_bytes(row) for row in out_rows
                )
                checkpoint_completed_sources()

                if max_batches and uploaded_batches >= max_batches:
                    return True
            return False

        for source in sources:
            key = checkpoint_key(source)
            if key in done:
                logger.info("Skipping already completed source: %s", source.name)
                continue
            logger.info("Processing source: %s", source.name)
            filter_spec = resolve_filter_spec(
                source=source,
                global_spec=global_filter_spec,
                legacy_spec=legacy_filter_spec,
            )
            context = AdapterContext(
                source_name=source.name,
                source_repo=source.repo,
                source_config=source.config,
                source_split=source.split,
                fields=source.fields or {},
                filter_spec=filter_spec,
                default_language=source.language or default_language,
                license=source.license,
                task_type=source.task_type,
                embed_media=(
                    source.embed_media
                    if source.embed_media is not None
                    else embed_media
                ),
                max_media_bytes=(
                    source.max_media_bytes
                    if source.max_media_bytes is not None
                    else max_media_bytes
                ),
                hf_token=token,
            )
            try:
                for item in iter_source_items(source):
                    mapped = adapter.map_item(item, context)
                    if not mapped:
                        continue
                    row = mapped.row
                    text_hash = hash_text(mapped.dedupe_key)
                    if not row.get("doc_id"):
                        row["doc_id"] = text_hash.hex()
                    pending.append((text_hash, row))

                    if len(pending) >= pending_limit:
                        new_pairs = store.filter_new(pending)
                        pending = []
                        add_new_pairs(new_pairs)
                        if flush_output():
                            logger.info(
                                "Reached max_batches=%s. Stopping early.",
                                max_batches,
                            )
                            return
            except Exception as exc:
                logger.warning("Failed source %s: %s", source.name, exc)
                if pending:
                    add_new_pairs(store.filter_new(pending))
                    pending = []
                    if flush_output():
                        logger.info(
                            "Reached max_batches=%s. Stopping early.",
                            max_batches,
                        )
                        return
                continue

            if pending:
                new_pairs = store.filter_new(pending)
                pending = []
                add_new_pairs(new_pairs)
                if flush_output():
                    logger.info(
                        "Reached max_batches=%s. Stopping early.",
                        max_batches,
                    )
                    return

            if cleanup_cache and source.kind == "hf" and source.repo:
                cleanup_hf_cache(source.repo)

            completed_source_keys.append(key)
            checkpoint_completed_sources()

        if pending:
            new_pairs = store.filter_new(pending)
            pending = []
            add_new_pairs(new_pairs)

        if out_rows and (not max_batches or uploaded_batches < max_batches):
            flush_output(force=True)
        checkpoint_completed_sources()

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
        help="Path to SQLite dedupe DB (modality-specific default)",
    )
    parser.add_argument("--refresh-dedupe", action="store_true", default=None, help="Refresh dedupe DB")
    parser.add_argument("--no-refresh-dedupe", action="store_false", dest="refresh_dedupe", default=None)
    parser.add_argument("--max-batches", type=int, help="Max number of shards to upload")
    parser.add_argument("--token", help="HF write token (defaults to cache or HF_TOKEN)")
    parser.add_argument(
        "--dataset",
        action="append",
        help="Only process specific dataset(s) by source name or repo (can be repeated)",
    )
    parser.add_argument("--inventory", help="Inventory JSONL path (from hf_inventory.py)")
    parser.add_argument("--include-regex", help="Regex to include repos from inventory")
    parser.add_argument("--exclude-regex", help="Regex to exclude repos from inventory")
    parser.add_argument("--cleanup-cache", action="store_true", default=None, help="Delete HF cache per source")
    parser.add_argument("--no-cleanup-cache", action="store_false", dest="cleanup_cache", default=None)
    parser.add_argument(
        "--checkpoint",
        help="Checkpoint file to skip completed sources (modality-specific default)",
    )
    parser.add_argument(
        "--embed-media",
        action="store_true",
        default=None,
        help="Embed audio/image bytes in target shards (default for ASR/OCR)",
    )
    parser.add_argument(
        "--no-embed-media",
        action="store_false",
        dest="embed_media",
        default=None,
        help="Keep source media paths only",
    )
    parser.add_argument(
        "--max-media-mb",
        type=float,
        help="Maximum size of one embedded audio/image item",
    )
    parser.add_argument(
        "--max-batch-mb",
        type=float,
        help="Flush a multimodal shard after this many MiB of embedded media",
    )

    args = parser.parse_args()

    config = load_config(args.config)
    target_repo = args.target_repo or config.get("target_repo")
    if not target_repo:
        print("Error: target_repo is required (via --target-repo or config).")
        sys.exit(1)

    raw_sources = config.get("sources") or []
    sources = parse_sources(raw_sources)

    include_re = re.compile(args.include_regex) if args.include_regex else None
    exclude_re = re.compile(args.exclude_regex) if args.exclude_regex else None
    if args.inventory:
        inventory_sources = load_inventory_sources(args.inventory, include_re, exclude_re)
        if inventory_sources:
            # Allow config sources to override inventory entries
            merged = list(inventory_sources)
            index = {
                (s.repo, s.config, s.split, s.adapter): i
                for i, s in enumerate(merged)
            }
            for s in sources:
                key = (s.repo, s.config, s.split, s.adapter)
                if key in index:
                    merged[index[key]] = s
                else:
                    merged.append(s)
            sources = merged

    if not sources:
        print("Error: No sources configured. Add `sources:` or provide --inventory.")
        sys.exit(1)

    if args.dataset:
        wanted = set(args.dataset)
        filtered = [s for s in sources if s.name in wanted or s.repo in wanted]
        if not filtered:
            print("Error: --dataset did not match any configured sources.")
            sys.exit(1)
        sources = filtered
    run_adapter = resolve_run_adapter(sources)

    options = config.get("options") or {}
    batch_size = args.batch_size or options.get("batch_size") or 50000
    incremental = args.incremental if args.incremental is not None else options.get("incremental")
    refresh_dedupe = args.refresh_dedupe if args.refresh_dedupe is not None else options.get("refresh_dedupe", True)
    max_batches = args.max_batches or options.get("max_batches")
    filters_raw = options.get("filters")
    global_filter_spec = FilterSpec.from_dict(filters_raw) if isinstance(filters_raw, dict) else None
    legacy_filter_spec = None
    if global_filter_spec is None:
        legacy_filter_spec = build_legacy_filter_spec(options)
    default_language = options.get("default_language")
    cleanup_cache = args.cleanup_cache if args.cleanup_cache is not None else options.get("cleanup_cache", True)
    embed_media = (
        args.embed_media
        if args.embed_media is not None
        else options.get("embed_media", run_adapter.name in {"asr", "ocr"})
    )
    default_max_media_mb = {
        "asr": 256,
        "ocr": 64,
    }.get(run_adapter.name)
    max_media_mb = (
        args.max_media_mb
        if args.max_media_mb is not None
        else options.get("max_media_mb", default_max_media_mb)
    )
    default_max_batch_mb = 256 if run_adapter.name in {"asr", "ocr"} else None
    max_batch_mb = (
        args.max_batch_mb
        if args.max_batch_mb is not None
        else options.get("max_batch_mb", default_max_batch_mb)
    )
    max_media_bytes = (
        int(float(max_media_mb) * 1024 * 1024)
        if max_media_mb
        else None
    )
    max_batch_bytes = (
        int(float(max_batch_mb) * 1024 * 1024)
        if max_batch_mb
        else None
    )
    if (
        run_adapter.name in {"asr", "ocr"}
        and not embed_media
        and cleanup_cache
    ):
        raise ValueError(
            "--no-embed-media cannot be combined with cache cleanup because "
            "source media paths may become invalid"
        )

    dedupe_store = (
        args.dedupe_store
        or options.get("dedupe_store")
        or (
            "data/dedupe_text_hashes.sqlite"
            if run_adapter.name == "text"
            else f"data/dedupe_{run_adapter.name}_hashes.sqlite"
        )
    )
    checkpoint_path = args.checkpoint or (
        "data/hf_merge_done.txt"
        if run_adapter.name == "text"
        else f"data/hf_merge_{run_adapter.name}_done.txt"
    )

    token = args.token or os.environ.get("HF_TOKEN") or get_token()
    if not token:
        print("Error: No Hugging Face token found. Use --token or set HF_TOKEN.")
        sys.exit(1)

    login(token=token)

    if cleanup_cache:
        datasets.disable_caching()

    merge_and_upload(
        sources=sources,
        repo_id=target_repo,
        token=token,
        batch_size=batch_size,
        incremental=incremental,
        refresh_dedupe=refresh_dedupe,
        dedupe_store_path=dedupe_store,
        max_batches=max_batches,
        global_filter_spec=global_filter_spec,
        legacy_filter_spec=legacy_filter_spec,
        default_language=default_language,
        cleanup_cache=cleanup_cache,
        checkpoint_path=checkpoint_path,
        embed_media=embed_media,
        max_media_bytes=max_media_bytes,
        max_batch_bytes=max_batch_bytes,
    )


if __name__ == "__main__":
    main()

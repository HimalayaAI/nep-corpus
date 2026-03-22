#!/usr/bin/env python3
"""Build a Devanagari-focused subset of wikimedia/wikipedia and upload to HF.

Languages are processed in the provided order. Default order prioritizes
languages used in Nepal (ne, new, mai, bh, dty), then other Devanagari scripts.

Output columns: text, source, language, doc_id, title, url.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from datasets import load_dataset, Dataset, Features, Value, get_dataset_config_names
from huggingface_hub import HfApi, get_token, login

# Ensure project root is on sys.path for scripts.* imports
import sys

project_root = str(Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.merge_datasets.quality_filters import normalize_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_DEFAULT_LANGS = ["ne", "new", "mai", "bh", "dty", "sa", "hi", "mr", "gom"]


def parse_list(raw: str) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_dump_date(config_name: str) -> Optional[int]:
    """Extract leading YYYYMMDD int from config name like 20231101.ne."""
    if not config_name:
        return None
    head = config_name.split(".", 1)[0]
    if head.isdigit() and len(head) == 8:
        try:
            return int(head)
        except ValueError:
            return None
    return None


def resolve_wikipedia_config(
    configs: Sequence[str], lang: str, preferred_dump: Optional[str]
) -> Optional[str]:
    if preferred_dump:
        preferred = f"{preferred_dump}.{lang}"
        if preferred in configs:
            return preferred

    candidates = [cfg for cfg in configs if cfg.endswith(f".{lang}") or cfg == lang]
    if not candidates:
        return None

    def sort_key(cfg: str) -> tuple[int, str]:
        date_val = _parse_dump_date(cfg) or 0
        return (date_val, cfg)

    return sorted(candidates, key=sort_key, reverse=True)[0]


def iter_wikipedia_rows(
    repo_id: str,
    config_name: str,
    split: str,
    download_first: bool,
) -> Iterator[Dict[str, Any]]:
    ds = load_dataset(
        repo_id, name=config_name, split=split, streaming=not download_first
    )
    for row in ds:
        yield row


def make_source_key(repo_id: str, config_name: str) -> str:
    return f"{repo_id}:{config_name}"


def get_max_shard_index_for_split(api: HfApi, repo_id: str, split_name: str) -> int:
    try:
        files = api.list_repo_files(repo_id=repo_id, repo_type="dataset")
    except Exception:
        return -1
    pattern = re.compile(rf"^data/{re.escape(split_name)}-(\\d+)-of-000000\\.parquet$")
    max_idx = -1
    for path in files:
        match = pattern.match(path)
        if not match:
            continue
        try:
            idx = int(match.group(1))
        except ValueError:
            continue
        if idx > max_idx:
            max_idx = idx
    return max_idx


def upload_parquet_batch(
    *,
    api: HfApi,
    repo_id: str,
    token: str,
    rows: List[Dict[str, Any]],
    shard_index: int,
    split_name: str,
) -> None:
    data_dict = {
        "text": [row.get("text") for row in rows],
        "source": [row.get("source") for row in rows],
        "language": [row.get("language") for row in rows],
        "doc_id": [row.get("doc_id") for row in rows],
        "title": [row.get("title") for row in rows],
        "url": [row.get("url") for row in rows],
    }

    features = Features(
        {
            "text": Value("string"),
            "source": Value("string"),
            "language": Value("string"),
            "doc_id": Value("string"),
            "title": Value("string"),
            "url": Value("string"),
        }
    )
    hf_dataset = Dataset.from_dict(data_dict, features=features)
    os.makedirs("data/hf_merge_export", exist_ok=True)
    parquet_path = (
        f"data/hf_merge_export/{split_name}-{shard_index:06d}-of-000000.parquet"
    )
    repo_path = f"data/{split_name}-{shard_index:06d}-of-000000.parquet"
    hf_dataset.to_parquet(parquet_path)

    api.upload_file(
        path_or_fileobj=parquet_path,
        path_in_repo=repo_path,
        repo_id=repo_id,
        repo_type="dataset",
        token=token,
    )

    os.remove(parquet_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export wikipedia subset to HF")
    parser.add_argument("--source-repo", default="wikimedia/wikipedia")
    parser.add_argument("--target-repo", required=True)
    parser.add_argument(
        "--languages",
        default=",".join(_DEFAULT_LANGS),
        help="Comma-separated language codes (processed in order)",
    )
    parser.add_argument("--split", default="train")
    parser.add_argument("--batch-size", type=int, default=100000)
    parser.add_argument(
        "--checkpoint",
        default="data/wikipedia_subset_done.txt",
        help="Checkpoint file of completed languages",
    )
    parser.add_argument("--max-batches", type=int)
    parser.add_argument(
        "--download-first",
        action="store_true",
        help="Download full split to cache before iterating",
    )
    parser.add_argument(
        "--dump",
        help="Preferred dump date (YYYYMMDD) if available for the language",
    )
    args = parser.parse_args()

    token = get_token() or os.getenv("HF_TOKEN")
    if not token:
        login()
        token = get_token()

    api = HfApi(token=token)
    try:
        api.repo_info(args.target_repo, repo_type="dataset", token=token)
    except Exception:
        api.create_repo(
            repo_id=args.target_repo,
            repo_type="dataset",
            exist_ok=True,
            token=token,
        )

    done: set[str] = set()
    if args.checkpoint and os.path.exists(args.checkpoint):
        with open(args.checkpoint, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    done.add(line)

    langs = parse_list(args.languages)
    configs = get_dataset_config_names(args.source_repo)
    for lang in langs:
        config_name = resolve_wikipedia_config(configs, lang, args.dump)
        if not config_name:
            logger.warning("No config found for language %s, skipping.", lang)
            continue

        source_key = make_source_key(args.source_repo, config_name)
        if source_key in done:
            logger.info("Skipping completed: %s", source_key)
            continue

        split_name = f"wiki_{lang}"
        shard_index = get_max_shard_index_for_split(api, args.target_repo, split_name) + 1
        logger.info(
            "Processing %s (lang=%s, split=%s, starting shard %s)",
            source_key,
            lang,
            args.split,
            shard_index,
        )

        batch: List[Dict[str, Any]] = []
        batches_written = 0

        for row_idx, row in enumerate(
            iter_wikipedia_rows(
                args.source_repo, config_name, args.split, args.download_first
            )
        ):
            text_raw = row.get("text")
            if not isinstance(text_raw, str):
                continue
            text_norm = normalize_text(text_raw)
            if not text_norm:
                continue

            doc_id = (
                row.get("id")
                or row.get("page_id")
                or row.get("doc_id")
                or f"{source_key}:{row_idx}"
            )
            title = row.get("title")
            url = row.get("url")

            batch.append(
                {
                    "text": text_norm,
                    "source": source_key,
                    "language": lang,
                    "doc_id": str(doc_id),
                    "title": str(title) if title is not None else None,
                    "url": str(url) if url is not None else None,
                }
            )

            if len(batch) >= args.batch_size:
                upload_parquet_batch(
                    api=api,
                    repo_id=args.target_repo,
                    token=token,
                    rows=batch,
                    shard_index=shard_index,
                    split_name=split_name,
                )
                shard_index += 1
                batches_written += 1
                batch = []
                if args.max_batches and batches_written >= args.max_batches:
                    break

        if batch:
            upload_parquet_batch(
                api=api,
                repo_id=args.target_repo,
                token=token,
                rows=batch,
                shard_index=shard_index,
                split_name=split_name,
            )

        if args.checkpoint:
            os.makedirs(os.path.dirname(args.checkpoint), exist_ok=True)
            with open(args.checkpoint, "a", encoding="utf-8") as f:
                f.write(source_key + "\n")

    logger.info("Wikipedia subset export complete.")


if __name__ == "__main__":
    main()

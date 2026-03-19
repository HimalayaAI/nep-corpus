#!/usr/bin/env python3
"""
Merge SFT-style datasets into a single HF repo in ShareGPT-style schema.

Expected output schema:
  conversations: list[{from: string, value: string}]
  source: string
  id: string
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from datasets import Dataset, Features, Sequence, Value, load_dataset
from huggingface_hub import HfApi, get_token

# Ensure project root is on sys.path for scripts.* imports
project_root = str(Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.merge_datasets.merge_corpus_to_hf import (
    get_max_shard_index,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


QUESTION_CANDIDATES = ["question", "prompt", "query", "instruction", "input"]
ANSWER_CANDIDATES = ["answer", "response", "output", "completion"]
INSTRUCTION_CANDIDATES = ["instruction", "prompt", "task"]
INPUT_CANDIDATES = ["input", "context", "additional_context"]
OUTPUT_CANDIDATES = ["output", "response", "answer", "completion"]


def iter_inventory(path: str) -> Iterator[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def iter_hf_dataset(repo: str, split: str, config: Optional[str]) -> Iterator[Dict[str, Any]]:
    if config:
        ds = load_dataset(repo, name=config, split=split, streaming=True)
    else:
        ds = load_dataset(repo, split=split, streaming=True)
    for row in ds:
        yield row


def _best_col(columns: List[str], candidates: List[str]) -> Optional[str]:
    lower_map = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    for cand in candidates:
        for col in columns:
            if cand in col.lower():
                return col
    return None


def infer_mapping(columns: List[str]) -> Dict[str, Optional[str]]:
    colset = {c.lower() for c in columns}
    if "conversations" in colset:
        return {"mode": "conversations", "key": _best_col(columns, ["conversations"])}
    if "messages" in colset:
        return {"mode": "messages", "key": _best_col(columns, ["messages"])}

    question = _best_col(columns, QUESTION_CANDIDATES)
    answer = _best_col(columns, ANSWER_CANDIDATES)
    if question and answer:
        return {"mode": "qa", "question": question, "answer": answer}

    instruction = _best_col(columns, INSTRUCTION_CANDIDATES)
    output = _best_col(columns, OUTPUT_CANDIDATES)
    if instruction and output:
        return {
            "mode": "instruction",
            "instruction": instruction,
            "input": _best_col(columns, INPUT_CANDIDATES),
            "output": output,
        }

    return {"mode": None}


def normalize_messages(messages: List[Dict[str, Any]]) -> Optional[List[Dict[str, str]]]:
    out: List[Dict[str, str]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or msg.get("from")
        content = msg.get("content") or msg.get("value")
        if role is None or content is None:
            continue
        role = str(role).lower()
        if role == "system":
            from_value = "system"
        elif role in ("user", "human", "prompt"):
            from_value = "human"
        else:
            from_value = "gpt"
        out.append({"from": from_value, "value": str(content)})
    return out if out else None


def add_system_message(convo: List[Dict[str, str]], system_message: Optional[str]) -> List[Dict[str, str]]:
    if not system_message:
        return convo
    if any(msg.get("from") == "system" for msg in convo):
        return convo
    return [{"from": "system", "value": system_message}] + convo


def convert_item(item: Dict[str, Any], mapping: Dict[str, Any], system_message: Optional[str]) -> Optional[List[Dict[str, str]]]:
    mode = mapping.get("mode")
    if mode == "conversations":
        key = mapping.get("key")
        convo = item.get(key) if key else None
        if isinstance(convo, list):
            normalized = normalize_messages(convo) or convo
            if isinstance(normalized, list) and normalized:
                return add_system_message(normalized, system_message)
    elif mode == "messages":
        key = mapping.get("key")
        msgs = item.get(key) if key else None
        if isinstance(msgs, list):
            normalized = normalize_messages(msgs)
            if normalized:
                return add_system_message(normalized, system_message)
    elif mode == "qa":
        q = item.get(mapping.get("question", ""))
        a = item.get(mapping.get("answer", ""))
        if q is None or a is None:
            return None
        q_str = str(q).strip()
        a_str = str(a).strip()
        if not q_str or not a_str:
            return None
        convo = [{"from": "human", "value": q_str}, {"from": "gpt", "value": a_str}]
        return add_system_message(convo, system_message)
    elif mode == "instruction":
        ins = item.get(mapping.get("instruction", ""))
        out = item.get(mapping.get("output", ""))
        if ins is None or out is None:
            return None
        ins_str = str(ins).strip()
        out_str = str(out).strip()
        if not ins_str or not out_str:
            return None
        input_col = mapping.get("input")
        if input_col:
            extra = item.get(input_col)
            if extra is not None and str(extra).strip():
                ins_str = f"{ins_str}\n\n{str(extra).strip()}"
        convo = [{"from": "human", "value": ins_str}, {"from": "gpt", "value": out_str}]
        return add_system_message(convo, system_message)
    return None


def upload_parquet_batch(
    *,
    api: HfApi,
    repo_id: str,
    token: str,
    rows: List[Dict[str, Any]],
    shard_index: int,
) -> None:
    features = Features(
        {
            "conversations": Sequence(
                {
                    "from": Value("string"),
                    "value": Value("string"),
                }
            ),
            "source": Value("string"),
            "id": Value("string"),
        }
    )
    dataset = Dataset.from_list(rows, features=features)
    os.makedirs("data/hf_merge_export", exist_ok=True)
    parquet_path = f"data/hf_merge_export/train-{shard_index:06d}-of-000000.parquet"
    repo_path = f"data/train-{shard_index:06d}-of-000000.parquet"
    dataset.to_parquet(parquet_path)
    api.upload_file(
        path_or_fileobj=parquet_path,
        path_in_repo=repo_path,
        repo_id=repo_id,
        repo_type="dataset",
        token=token,
    )
    os.remove(parquet_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge SFT datasets into ShareGPT format")
    parser.add_argument("--inventory", required=True, help="SFT inventory JSONL")
    parser.add_argument("--target-repo", required=True, help="Target HF repo (org/name)")
    parser.add_argument("--batch-size", type=int, default=50000, help="Rows per shard")
    parser.add_argument("--max-batches", type=int, help="Max shards to upload")
    parser.add_argument("--max-rows", type=int, help="Max rows total")
    parser.add_argument("--dataset", action="append", help="Only include specific repo_id")
    parser.add_argument("--system-message", help="System message to prepend if missing")
    parser.add_argument("--token", help="HF token (defaults to cache or HF_TOKEN)")
    args = parser.parse_args()

    token = args.token or get_token()
    api = HfApi()

    repo_exists = True
    try:
        api.repo_info(args.target_repo, repo_type="dataset")
    except Exception:
        repo_exists = False
        api.create_repo(args.target_repo, repo_type="dataset", private=True)

    shard_index = get_max_shard_index(api, args.target_repo) + 1 if repo_exists else 1
    logger.info("Starting shard index: %s", shard_index)

    wanted = set(args.dataset) if args.dataset else None

    out_rows: List[Dict[str, Any]] = []
    uploaded = 0
    total = 0

    for row in iter_inventory(args.inventory):
        repo_id = row.get("repo_id")
        split = row.get("split", "train")
        config = row.get("config")
        if not repo_id:
            continue
        if wanted and repo_id not in wanted:
            continue

        logger.info("Processing source: %s", repo_id)
        try:
            # infer columns from inventory if available, else from first row
            columns = []
            features = row.get("features")
            if isinstance(features, dict):
                columns = list(features.keys()) if "columns" not in features else features.get("columns") or []
            mapping = infer_mapping(columns)

            # fallback: peek first row to infer
            if not mapping.get("mode"):
                ds = load_dataset(repo_id, name=config, split=split, streaming=True)
                first = next(iter(ds), None)
                if isinstance(first, dict):
                    mapping = infer_mapping(list(first.keys()))

            if not mapping.get("mode"):
                logger.warning("Skipping %s; unable to infer mapping", repo_id)
                continue

            for item in iter_hf_dataset(repo_id, split=split, config=config):
                convo = convert_item(item, mapping, args.system_message)
                if not convo:
                    continue
                out_rows.append(
                    {
                        "conversations": convo,
                        "source": repo_id,
                        "id": str(uuid.uuid4()),
                    }
                )
                total += 1
                if args.max_rows and total >= args.max_rows:
                    break

                if len(out_rows) >= args.batch_size:
                    upload_parquet_batch(
                        api=api,
                        repo_id=args.target_repo,
                        token=token,
                        rows=out_rows,
                        shard_index=shard_index,
                    )
                    out_rows = []
                    shard_index += 1
                    uploaded += 1
                    if args.max_batches and uploaded >= args.max_batches:
                        break

            if args.max_rows and total >= args.max_rows:
                break
            if args.max_batches and uploaded >= args.max_batches:
                break
        except Exception as exc:
            logger.warning("Failed processing %s: %s", repo_id, exc)
            continue

    if out_rows and (not args.max_batches or uploaded < args.max_batches):
        upload_parquet_batch(
            api=api,
            repo_id=args.target_repo,
            token=token,
            rows=out_rows,
            shard_index=shard_index,
        )
        uploaded += 1

    logger.info("SFT merge complete. Uploaded %s shard(s). Total rows: %s", uploaded, total)


if __name__ == "__main__":
    main()

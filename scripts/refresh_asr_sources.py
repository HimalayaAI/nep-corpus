#!/usr/bin/env python3
"""
Refresh ASR source schemas by sampling a row or two from each dataset entry.

Reads a JSONL file (e.g., sources/asr_transcription_sources.jsonl),
loads each dataset split in streaming mode, inspects a sample row, and
updates the entry with:
  - sample_keys
  - mapping_suggested (text/audio)
  - schema_sampled / sample_error

Usage:
  python scripts/refresh_asr_sources.py \
    --in sources/asr_transcription_sources.jsonl \
    --out sources/asr_transcription_sources.jsonl \
    --max-samples 2 \
    --trust-remote-code
"""

from __future__ import annotations

import argparse
import json
import re
import signal
from typing import Any, Dict, List, Optional, Tuple

from datasets import load_dataset
import requests

DATASETS_SERVER = "https://datasets-server.huggingface.co"

TEXT_FIELD_CANDIDATES = [
    "sentence",
    "transcription",
    "raw_transcription",
    "text",
    "normalized_text",
    "utterance",
    "transcript",
    "label",
]

AUDIO_HINT_KEYS = {"path", "array", "sampling_rate", "bytes"}
AUDIO_FIELD_CANDIDATES = [
    "audio",
    "file_path",
    "audio_path",
    "hf_audio_path",
    "utterance",
    "flac",
    "wav",
    "mp3",
    "path",
    "file",
]
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")


def _pick_text_field(row: Dict[str, Any], preferred: Optional[str]) -> Optional[str]:
    if preferred and preferred in row and isinstance(row.get(preferred), str):
        return preferred

    # Exact name match first
    lower_map = {k.lower(): k for k in row.keys()}
    for cand in TEXT_FIELD_CANDIDATES:
        if cand in lower_map and isinstance(row.get(lower_map[cand]), str):
            return lower_map[cand]

    # Fallback only to strings that look like Nepali transcript content. Picking
    # the first string silently maps IDs such as WebDataset `__key__` as text.
    for k, v in row.items():
        if isinstance(v, str) and _DEVANAGARI_RE.search(v):
            return k
    return None


def _pick_audio_field(row: Dict[str, Any], preferred: Optional[str]) -> Optional[str]:
    if preferred and preferred in row:
        return preferred

    lower_map = {k.lower(): k for k in row.keys()}
    for candidate in AUDIO_FIELD_CANDIDATES:
        key = lower_map.get(candidate)
        if key and row.get(key) is not None:
            return key

    for k, v in row.items():
        if isinstance(v, dict) and AUDIO_HINT_KEYS.intersection(v.keys()):
            return k
    return None


def _load_sample(
    repo_id: str,
    config: Optional[str],
    split: str,
    trust_remote_code: bool,
    max_samples: int,
    timeout_s: int,
    allow_load_dataset: bool,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    # 1) Try datasets-server rows API first (fast, no downloads)
    try:
        params = {"dataset": repo_id, "split": split, "offset": 0, "length": max_samples}
        if config:
            params["config"] = config
        resp = requests.get(f"{DATASETS_SERVER}/rows", params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            rows = data.get("rows") or []
            if rows:
                first = rows[0].get("row")
                if isinstance(first, dict):
                    return first, None
    except Exception:
        pass

    if not allow_load_dataset:
        return None, f"rows_api_unavailable ({resp.status_code if 'resp' in locals() else 'no_response'})"

    # 2) Fallback: streaming load_dataset with per-dataset timeout
    def _handle_timeout(signum, frame):
        raise TimeoutError("timeout")

    old_handler = signal.signal(signal.SIGALRM, _handle_timeout)
    signal.alarm(timeout_s)
    try:
        kwargs = {"split": split, "streaming": True, "trust_remote_code": trust_remote_code}
        if config and config != "default":
            ds = load_dataset(repo_id, name=config, **kwargs)
        else:
            ds = load_dataset(repo_id, **kwargs)

        it = iter(ds)
        for _ in range(max_samples):
            row = next(it, None)
            if row:
                return row, None
        return None, "no_rows"
    except Exception as exc:
        return None, str(exc)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def refresh_entry(
    entry: Dict[str, Any],
    trust_remote_code: bool,
    max_samples: int,
    timeout_s: int,
    allow_load_dataset: bool,
) -> Dict[str, Any]:
    repo_id = entry.get("repo_id")
    config = entry.get("config")
    split = entry.get("split", "train")
    preferred_text = (entry.get("mapping_suggested") or {}).get("text")
    preferred_audio = (entry.get("mapping_suggested") or {}).get("audio")

    row, err = _load_sample(
        repo_id,
        config,
        split,
        trust_remote_code,
        max_samples,
        timeout_s,
        allow_load_dataset,
    )
    if err:
        entry["schema_sampled"] = False
        entry["sample_error"] = err
        entry["review_needed"] = True
        entry["triage_notes"] = f"Sample failed: {err}"
        return entry

    entry["schema_sampled"] = True
    entry["sample_error"] = None
    entry["sample_keys"] = list(row.keys())

    text_field = _pick_text_field(row, preferred_text)
    audio_field = _pick_audio_field(row, preferred_audio)
    mapping = entry.get("mapping_suggested") or {}
    mapping["text"] = text_field
    mapping["audio"] = audio_field
    entry["mapping_suggested"] = mapping

    # If text field missing, keep review flag high
    if not text_field:
        entry["review_needed"] = True
        entry["triage_notes"] = "Could not detect transcript field from sample row."
    else:
        entry["triage_notes"] = "Schema sampled from streaming row."

    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh ASR source schemas by sampling rows")
    parser.add_argument("--in", dest="input_path", required=True, help="Input JSONL")
    parser.add_argument("--out", dest="output_path", required=True, help="Output JSONL")
    parser.add_argument("--max-samples", type=int, default=1, help="Rows to try per dataset")
    parser.add_argument("--trust-remote-code", action="store_true", help="Enable trust_remote_code")
    parser.add_argument("--timeout", type=int, default=20, help="Per-dataset timeout in seconds")
    parser.add_argument(
        "--allow-load-dataset",
        action="store_true",
        help="Fallback to datasets.load_dataset if rows API fails (slower)",
    )
    args = parser.parse_args()

    entries: List[Dict[str, Any]] = []
    with open(args.input_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))

    updated: List[Dict[str, Any]] = []
    for entry in entries:
        updated.append(
            refresh_entry(
                entry,
                args.trust_remote_code,
                args.max_samples,
                args.timeout,
                args.allow_load_dataset,
            )
        )

    with open(args.output_path, "w", encoding="utf-8") as f:
        for entry in updated:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"Refreshed {len(updated)} entries -> {args.output_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Discover Nepali ASR datasets on Hugging Face and emit JSONL candidate sources
for transcription-only corpus builds.

This script:
  1) searches the HF Hub for datasets matching a query (default: "nepali asr")
  2) inspects configs/splits via the datasets-server API (no remote code exec)
  3) detects likely transcript fields (sentence/transcription/text/etc.)
  4) writes JSONL entries with suggested mappings

Example:
  python scripts/find_asr_datasets.py \
    --query "nepali asr" \
    --max-results 200 \
    --out sources/asr_transcription_sources.jsonl \
    --extra google/fleurs,mozilla-foundation/common_voice_5_0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from huggingface_hub import HfApi, list_datasets

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

AUDIO_FIELD_CANDIDATES = [
    "audio",
    "file_path",
    "audio_path",
    "utterance",
    "path",
    "file",
    "wav",
    "mp3",
    "audio_filepath",
]

NEPALI_CONFIG_HINTS = [
    "ne",
    "ne_np",
    "ne-np",
    "nep",
    "nepali",
]

# Manual schema fallbacks for datasets that don't support the datasets-server viewer.
# These are best-effort based on dataset cards.
SPECIAL_OVERRIDES = {
    "google/fleurs": {
        "text_field": "transcription",
        "audio_field": "audio",
        "configs": ["ne_np"],
        "splits": ["train", "validation", "test"],
    },
    # Common Voice (HF hosted) typically uses "sentence" for transcripts.
    "mozilla-foundation/common_voice_13_0": {
        "text_field": "sentence",
        "audio_field": "audio",
        "configs": ["ne"],
        "splits": ["train", "validation", "test"],
    },
    "mozilla-foundation/common_voice_17_0": {
        "text_field": "sentence",
        "audio_field": "audio",
        "configs": ["ne"],
        "splits": ["train", "validation", "test"],
    },
}


@dataclass
class DatasetCandidate:
    repo_id: str
    config: str
    split: str
    features: Dict[str, Any]
    text_field: str
    audio_field: Optional[str]
    license: Optional[str]

    def to_json(self) -> Dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "config": self.config,
            "split": self.split,
            "features": list(self.features.keys()),
            "provider": "huggingface",
            "adapter": "asr",
            "collection": "nepali_asr",
            "language": "ne",
            "mapping_suggested": {
                "text": self.text_field,
                "audio": self.audio_field,
                "url": None,
                "language": None,
                "doc_id": None,
            },
            "usable": False,
            "modality": "audio_text",
            "task_type": "asr_transcript",
            "quality_bucket": "review",
            "review_needed": True,
            "triage_notes": "Schema discovered from datasets-server; verify license and text quality.",
            "notes": f"ASR dataset; transcripts in `{self.text_field}`.",
            "license": self.license,
            "url": f"https://huggingface.co/datasets/{self.repo_id}",
        }


def _request_json(url: str, params: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def _get_config_names(api: HfApi, repo_id: str) -> List[str]:
    try:
        info = api.dataset_info(repo_id)
        if info and info.config_names:
            return list(info.config_names)
    except Exception:
        pass

    splits = _request_json(f"{DATASETS_SERVER}/splits", {"dataset": repo_id})
    if splits and "splits" in splits:
        configs = sorted({s.get("config") for s in splits["splits"] if s.get("config")})
        if configs:
            return configs

    info = _request_json(f"{DATASETS_SERVER}/info", {"dataset": repo_id})
    if not info:
        return ["default"]

    # datasets-server may return configs under "config_names" or "configs"
    if "config_names" in info and isinstance(info["config_names"], list):
        return info["config_names"]
    if "configs" in info and isinstance(info["configs"], list):
        names = []
        for cfg in info["configs"]:
            name = cfg.get("config_name")
            if name:
                names.append(name)
        return names or ["default"]

    return ["default"]


def _get_splits(repo_id: str, config: str) -> List[str]:
    data = _request_json(
        f"{DATASETS_SERVER}/splits",
        {"dataset": repo_id, "config": config},
    )
    if not data or "splits" not in data:
        return ["train"]
    splits = []
    for split in data["splits"]:
        name = split.get("split")
        if name:
            splits.append(name)
    return splits or ["train"]


def _get_features(repo_id: str, config: str, split: str) -> Dict[str, Any]:
    data = _request_json(
        f"{DATASETS_SERVER}/features",
        {"dataset": repo_id, "config": config, "split": split},
    )
    if data:
        features = data.get("features")
        if isinstance(features, dict):
            return features

    # Fallback: some datasets expose features via /info even if /features 404s
    info = _request_json(f"{DATASETS_SERVER}/info", {"dataset": repo_id, "config": config})
    if not info:
        return {}

    dataset_info = info.get("dataset_info") or info.get("dataset_infos")
    if isinstance(dataset_info, dict):
        # dataset_infos might be keyed by config
        if "features" in dataset_info:
            features = dataset_info.get("features")
            if isinstance(features, dict):
                return features
        # Try config key
        cfg_info = dataset_info.get(config)
        if isinstance(cfg_info, dict):
            features = cfg_info.get("features")
            if isinstance(features, dict):
                return features
    return {}


def _pick_text_field(features: Dict[str, Any]) -> Optional[str]:
    if not features:
        return None
    lower_map = {k.lower(): k for k in features.keys()}
    for cand in TEXT_FIELD_CANDIDATES:
        if cand in lower_map:
            return lower_map[cand]

    # fall back: pick first string-like field
    for name, spec in features.items():
        if isinstance(spec, dict) and spec.get("_type") == "Value":
            dtype = (spec.get("dtype") or "").lower()
            if "string" in dtype:
                return name
    return None


def _pick_audio_field(features: Dict[str, Any]) -> Optional[str]:
    if not features:
        return None
    lower_map = {k.lower(): k for k in features.keys()}
    for cand in AUDIO_FIELD_CANDIDATES:
        if cand in lower_map:
            return lower_map[cand]
    return None


def _looks_nepali_config(config: str) -> bool:
    lc = config.lower()
    return any(hint in lc for hint in NEPALI_CONFIG_HINTS)


def discover_datasets(query: str, max_results: int) -> List[str]:
    ids = []
    for ds in list_datasets(search=query, full=True):
        ids.append(ds.id)
        if len(ids) >= max_results:
            break
    return ids


def collect_candidates(
    dataset_ids: Iterable[str],
    only_nepali_configs: bool = True,
) -> List[DatasetCandidate]:
    api = HfApi()
    results: List[DatasetCandidate] = []

    for repo_id in dataset_ids:
        # Manual overrides for datasets that don't support viewer endpoints
        if repo_id in SPECIAL_OVERRIDES:
            override = SPECIAL_OVERRIDES[repo_id]
            for cfg in override.get("configs", ["default"]):
                if only_nepali_configs and not _looks_nepali_config(cfg):
                    continue
                for split in override.get("splits", ["train"]):
                    results.append(
                        DatasetCandidate(
                            repo_id=repo_id,
                            config=cfg,
                            split=split,
                            features={},
                            text_field=override["text_field"],
                            audio_field=override.get("audio_field"),
                            license=None,
                        )
                    )
            continue

        configs = _get_config_names(api, repo_id)
        for config in configs:
            if only_nepali_configs and not _looks_nepali_config(config) and config != "default":
                continue
            splits = _get_splits(repo_id, config)
            if not splits:
                splits = ["train"]

            # grab features from the first split; assume same schema for all
            features = _get_features(repo_id, config, splits[0])
            text_field = _pick_text_field(features)
            if not text_field:
                continue
            audio_field = _pick_audio_field(features)

            try:
                info = api.dataset_info(repo_id)
                license = info.card_data.get("license") if info and info.card_data else None
            except Exception:
                license = None

            for split in splits:
                results.append(
                    DatasetCandidate(
                        repo_id=repo_id,
                        config=config,
                        split=split,
                        features=features,
                        text_field=text_field,
                        audio_field=audio_field,
                        license=license,
                    )
                )

    return results


def write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover Nepali ASR datasets and schemas on HF")
    parser.add_argument("--query", default="nepali asr", help="HF dataset search query")
    parser.add_argument("--max-results", type=int, default=200, help="Max HF results to scan")
    parser.add_argument("--out", default="sources/asr_transcription_sources.jsonl", help="Output JSONL path")
    parser.add_argument(
        "--extra",
        default="",
        help="Comma-separated extra dataset ids to include (e.g., google/fleurs,mozilla-foundation/common_voice_5_0)",
    )
    parser.add_argument(
        "--all-configs",
        action="store_true",
        help="Do not restrict to Nepali-looking config names",
    )
    args = parser.parse_args()

    dataset_ids = discover_datasets(args.query, args.max_results)

    if args.extra.strip():
        dataset_ids.extend([x.strip() for x in args.extra.split(",") if x.strip()])

    # de-dup
    dataset_ids = list(dict.fromkeys(dataset_ids))

    candidates = collect_candidates(
        dataset_ids,
        only_nepali_configs=not args.all_configs,
    )

    write_jsonl(args.out, [c.to_json() for c in candidates])
    print(f"Wrote {len(candidates)} candidates to {args.out}")


if __name__ == "__main__":
    main()

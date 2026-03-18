#!/usr/bin/env python3
"""
Sample dataset quality via HF dataset-server API without downloading the full dataset.
Reports length/word stats, Devanagari ratio, and pass rate for configured filters.
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

# Ensure project root is on sys.path for scripts.* imports
project_root = str(Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from scripts.merge_datasets.merge_corpus_to_hf import (
    build_legacy_filter_spec,
    parse_sources,
    get_field_value,
)
from scripts.merge_datasets.quality_filters import (
    FilterSpec,
    compute_metrics,
    normalize_text,
    passes_quality,
)


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def fetch_info(dataset: str) -> Dict[str, Any]:
    url = f"https://datasets-server.huggingface.co/info?dataset={dataset}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_rows(
    dataset: str,
    config: str,
    split: str,
    offset: int,
    length: int,
    max_retries: int = 5,
    backoff_s: float = 0.5,
) -> Dict[str, Any]:
    url = (
        "https://datasets-server.huggingface.co/rows?dataset={dataset}"
        "&config={config}&split={split}&offset={offset}&length={length}"
    ).format(dataset=dataset, config=config, split=split, offset=offset, length=length)
    for attempt in range(max_retries):
        resp = requests.get(url, timeout=30)
        if resp.status_code == 429:
            sleep_s = backoff_s * (2 ** attempt)
            time.sleep(sleep_s)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def sample_texts(
    dataset: str,
    config: str,
    split: str,
    text_field: Any,
    total_samples: int,
    block_size: int,
    seed: int,
) -> List[str]:
    info = fetch_info(dataset)
    split_info = (
        info.get("dataset_info", {})
        .get(config, {})
        .get("splits", {})
        .get(split)
    )
    if not split_info:
        raise RuntimeError(f"Split not found: {dataset} {split}")
    num_rows = split_info.get("num_rows") or split_info.get("num_examples")
    if not num_rows:
        raise RuntimeError(f"No row count for {dataset}")

    random.seed(seed)
    blocks = max(1, total_samples // block_size)
    starts = [random.randint(0, max(0, num_rows - block_size)) for _ in range(blocks)]

    texts: List[str] = []
    for start in starts:
        payload = fetch_rows(dataset, config, split, start, block_size)
        for row in payload.get("rows", []):
            raw_row = row.get("row", {})
            text = get_field_value(raw_row, text_field)
            if text is None:
                text = ""
            texts.append(str(text))
            if len(texts) >= total_samples:
                return texts
    return texts[:total_samples]


def summarize_metrics(metrics: List[Dict[str, float]]) -> Dict[str, float]:
    lengths = [m["length"] for m in metrics]
    words = [m["word_count"] for m in metrics]
    dev = [m["devanagari_ratio"] for m in metrics]

    def pct(cond):
        return 100.0 * sum(1 for x in cond if x) / len(metrics) if metrics else 0.0

    return {
        "count": len(metrics),
        "len_min": min(lengths) if lengths else 0,
        "len_p50": statistics.median(lengths) if lengths else 0,
        "len_p90": sorted(lengths)[int(0.9 * len(lengths)) - 1] if lengths else 0,
        "len_max": max(lengths) if lengths else 0,
        "words_min": min(words) if words else 0,
        "words_p50": statistics.median(words) if words else 0,
        "words_p90": sorted(words)[int(0.9 * len(words)) - 1] if words else 0,
        "words_max": max(words) if words else 0,
        "pct_len_lt_20": pct(l < 20 for l in lengths),
        "pct_len_lt_50": pct(l < 50 for l in lengths),
        "pct_len_lt_100": pct(l < 100 for l in lengths),
        "pct_words_lt_5": pct(w < 5 for w in words),
        "pct_words_lt_10": pct(w < 10 for w in words),
        "pct_dev_lt_0_3": pct(r < 0.3 for r in dev),
        "pct_dev_lt_0_5": pct(r < 0.5 for r in dev),
        "pct_dev_ge_0_7": pct(r >= 0.7 for r in dev),
    }


def resolve_filter_spec(
    *,
    source_filters: Optional[Dict[str, Any]],
    global_spec: Optional[FilterSpec],
    legacy_spec: Optional[FilterSpec],
) -> Optional[FilterSpec]:
    spec = global_spec if global_spec is not None else legacy_spec
    if source_filters:
        spec = spec.merge(source_filters) if spec else FilterSpec.from_dict(source_filters)
    return spec


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample dataset quality via HF dataset-server API")
    parser.add_argument("--config", required=True, help="Path to merge_config.yml")
    parser.add_argument("--samples", type=int, default=100, help="Number of samples per dataset")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--block-size", type=int, default=10)
    parser.add_argument("--dataset", help="Optional: only analyze this dataset name")
    args = parser.parse_args()

    config = load_config(args.config)
    sources = parse_sources(config.get("sources") or [])
    if args.dataset:
        sources = [s for s in sources if s.name == args.dataset]
        if not sources:
            raise SystemExit(f"No source matched dataset: {args.dataset}")

    options = config.get("options") or {}
    filters_raw = options.get("filters")
    global_filter_spec = FilterSpec.from_dict(filters_raw) if isinstance(filters_raw, dict) else None
    legacy_filter_spec = None
    if global_filter_spec is None:
        legacy_filter_spec = build_legacy_filter_spec(options)

    for source in sources:
        if source.kind != "hf" or not source.repo:
            continue
        print(f"\n=== {source.repo} ({source.split}) ===")

        filter_spec = resolve_filter_spec(
            source_filters=source.filters,
            global_spec=global_filter_spec,
            legacy_spec=legacy_filter_spec,
        )

        text_field = source.fields.get("text", "text") if source.fields else "text"
        texts = sample_texts(
            dataset=source.repo,
            config="default",
            split=source.split,
            text_field=text_field,
            total_samples=args.samples,
            block_size=args.block_size,
            seed=args.seed,
        )

        metrics = []
        passed = 0
        for text in texts:
            text_norm = normalize_text(text)
            m = compute_metrics(text_norm)
            metrics.append(m)
            if passes_quality(text_norm, filter_spec):
                passed += 1

        summary = summarize_metrics(metrics)
        for k, v in summary.items():
            print(f"{k}: {v}")
        pass_rate = 100.0 * passed / len(texts) if texts else 0.0
        print(f"pass_rate: {pass_rate:.2f}% ({passed}/{len(texts)})")


if __name__ == "__main__":
    main()

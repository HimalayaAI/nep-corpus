#!/usr/bin/env python3
"""
Compile Nepali embedding-training pairs from many HF datasets into one HF dataset repo.

Like merge_corpus_to_hf.py (YAML source list, streaming, hash dedupe, incremental shard
append, per-source checkpoint), but with a pair schema for retrieval / embedding training:

  query (str)        anchor text (question, title, sentence, English side of bitext, ...)
  positive (str)     relevant text (passage, answer, article, entailed sentence, translation, ...)
  negatives (list)   optional explicit negatives (e.g. NLI contradictions); mined later on GPU
  source (str)       source dataset id
  task_type (str)    qa | title_body | summary_text | instruction | nli | sts | bitext | paraphrase
  script (str)       deva | roman | mixed | latin (of the query)
  origin (str)       human | synthetic | translated | mined | unknown
  license (str)      source license

Benchmark hygiene: every query/positive/negative that matches (after NFC + whitespace
normalisation) a string in the configured exclusion datasets (e.g. NEB test sets) is dropped.

Usage:
  python scripts/merge_datasets/retrieval_pairs_to_hf.py --config scripts/merge_datasets/retrieval_pairs_config.yml
  python scripts/merge_datasets/retrieval_pairs_to_hf.py --config ... --dry-run   # build shards locally, no upload
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import yaml
from datasets import Dataset, get_dataset_config_names, load_dataset
from huggingface_hub import HfApi, hf_hub_download

project_root = str(Path(__file__).resolve().parents[2])
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from scripts.merge_datasets.quality_filters import devanagari_ratio  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("pairs")

_WS = re.compile(r"\s+")
_LATIN = re.compile(r"[A-Za-z]")
COLUMNS = ["query", "positive", "negatives", "source", "task_type", "script", "origin", "license"]


def norm(s: Any) -> str:
    if s is None:
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFC", str(s))).strip()


def h(s: str) -> bytes:
    return hashlib.blake2b(s.encode("utf-8", "ignore"), digest_size=16).digest()


def script_of(text: str) -> str:
    dr = devanagari_ratio(text)
    has_latin = bool(_LATIN.search(text))
    if dr >= 0.8:
        return "deva"
    if dr <= 0.05 and has_latin:
        return "latin"
    if dr > 0.05 and has_latin:
        return "mixed"
    return "deva" if dr > 0.5 else "latin"


def as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, str):
        v = v.strip()
        if v[:1] in "[{":
            for loader in (json.loads, ast.literal_eval):
                try:
                    out = loader(v)
                    return out if isinstance(out, list) else [out]
                except Exception:
                    pass
        return [v]
    return list(v)


# ---------------------------------------------------------------- row transforms
# Each transform yields dicts with query / positive / negatives (+ optional overrides).

def t_pair(row, f):
    yield {"query": row.get(f["query"]), "positive": row.get(f["positive"]),
           "negatives": [row.get(n) for n in f.get("negatives", []) if row.get(n)]}


def t_squad_nested(row, f):
    ctx = row.get(f["context"])
    for qa in as_list(row.get(f["qas"])):
        q = qa.get("question") if isinstance(qa, dict) else None
        if q:
            yield {"query": q, "positive": ctx, "negatives": []}


def t_sharegpt(row, f):
    conv = as_list(row.get(f.get("conversations", "conversations")))
    human = next((m.get("value") for m in conv if isinstance(m, dict) and m.get("from") in ("human", "user")), None)
    gpt = next((m.get("value") for m in conv if isinstance(m, dict) and m.get("from") in ("gpt", "assistant")), None)
    pos = row.get(f["context"]) if f.get("context") and row.get(f["context"]) else gpt
    if human and pos:
        yield {"query": human, "positive": pos, "negatives": []}


def t_nli_labeled(row, f):
    lab = str(row.get(f["label"])).lower()
    if lab in ("entailment", "0"):
        yield {"query": row.get(f["premise"]), "positive": row.get(f["hypothesis"]), "negatives": []}


def t_sts(row, f):
    try:
        score = float(row.get(f["label"]))
    except (TypeError, ValueError):
        return
    if score >= f.get("min_score", 4.0):
        yield {"query": row.get(f["s1"]), "positive": row.get(f["s2"]), "negatives": []}


TRANSFORMS = {"pair": t_pair, "squad_nested": t_squad_nested, "sharegpt": t_sharegpt,
              "nli_labeled": t_nli_labeled, "sts": t_sts}


# ---------------------------------------------------------------- loading

def iter_rows(src: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    if src.get("parquet_file"):
        import pyarrow.parquet as pq
        path = hf_hub_download(src["repo"], src["parquet_file"], repo_type="dataset")
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(batch_size=2000):
            yield from batch.to_pylist()
        return
    configs = src.get("configs") or [src.get("config")]
    for cfg in configs:
        ds = load_dataset(src["repo"], cfg, split=src.get("split", "train"), streaming=True)
        yield from ds


def load_exclusion(cfg: Dict[str, Any], cache: Path) -> set:
    if cache.exists():
        s = set(cache.read_text(encoding="utf-8").splitlines())
        log.info("exclusion set loaded from cache: %d strings", len(s))
        return s
    excl = set()
    for repo in cfg.get("exclude_datasets", []):
        try:
            names = get_dataset_config_names(repo)
        except Exception:
            names = [None]
        for c in names:
            try:
                dd = load_dataset(repo, c)
            except Exception as e:
                log.warning("exclusion: skip %s/%s (%s)", repo, c, str(e)[:80])
                continue
            for split in dd.values():
                for col, feat in split.features.items():
                    if getattr(feat, "dtype", None) == "string":
                        excl.update(norm(x) for x in split[col] if x)
        log.info("exclusion after %s: %d", repo, len(excl))
    cache.write_text("\n".join(excl), encoding="utf-8")
    return excl


# ---------------------------------------------------------------- main loop

def keep(rec: Dict[str, Any], flt: Dict[str, Any]) -> bool:
    q, p = rec["query"], rec["positive"]
    if not q or not p or q == p:
        return False
    if len(q) < flt.get("min_query_chars", 5) or len(p) < flt.get("min_positive_chars", 10):
        return False
    if len(q) > flt.get("max_query_chars", 1000) or len(p) > flt.get("max_positive_chars", 6000):
        return False
    side = flt.get("nepali_side", "positive")  # which side must be Nepali (Devanagari)
    if side and devanagari_ratio(rec[side]) < flt.get("min_devanagari_ratio", 0.5):
        return False
    return True


def run(cfg: Dict[str, Any], dry_run: bool, workdir: Path, token: Optional[str]) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    excl = load_exclusion(cfg, workdir / "exclusion_strings.txt")
    db = sqlite3.connect(workdir / "dedupe.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS seen (h BLOB PRIMARY KEY)")
    done_file = workdir / "sources_done.txt"
    done = set(done_file.read_text().splitlines()) if done_file.exists() else set()
    api = HfApi(token=token)
    repo = cfg["target_repo"]
    if not dry_run:
        api.create_repo(repo, repo_type="dataset", private=cfg.get("private", True), exist_ok=True)
    shard_rows = cfg.get("shard_rows", 100_000)
    existing = sorted(p.name for p in (workdir / "shards").glob("train-*.parquet")) if (workdir / "shards").exists() else []
    shard_idx = len(existing)
    (workdir / "shards").mkdir(exist_ok=True)
    buf: List[Dict[str, Any]] = []
    stats = json.loads((workdir / "stats.json").read_text()) if (workdir / "stats.json").exists() else {}

    def flush():
        nonlocal shard_idx, buf
        if not buf:
            return
        path = workdir / "shards" / f"train-{shard_idx:05d}.parquet"
        Dataset.from_list(buf).to_parquet(str(path))
        if not dry_run:
            api.upload_file(path_or_fileobj=str(path), path_in_repo=f"data/{path.name}", repo_id=repo,
                            repo_type="dataset", commit_message=f"Add shard {path.name} ({len(buf)} rows)")
        log.info("shard %s: %d rows%s", path.name, len(buf), "" if dry_run else " (uploaded)")
        shard_idx += 1
        buf = []

    for src in cfg["sources"]:
        name = src["name"]
        if name in done:
            log.info("skip (done): %s", name)
            continue
        fn = TRANSFORMS[src["transform"]]
        flt = {**cfg.get("filters", {}), **src.get("filters", {})}
        cap = src.get("max_rows")
        kept = dropped_excl = dropped_dup = dropped_flt = seen_rows = 0
        try:
            for row in iter_rows(src):
                seen_rows += 1
                for rec in fn(row, src["fields"]):
                    rec["query"], rec["positive"] = norm(rec["query"]), norm(rec["positive"])
                    rec["negatives"] = [n for n in (norm(x) for x in rec.get("negatives", [])) if n]
                    if not keep(rec, flt):
                        dropped_flt += 1
                        continue
                    if rec["query"] in excl or rec["positive"] in excl or any(n in excl for n in rec["negatives"]):
                        dropped_excl += 1
                        continue
                    key = h(rec["query"] + "\x1f" + rec["positive"])
                    try:
                        db.execute("INSERT INTO seen VALUES (?)", (key,))
                    except sqlite3.IntegrityError:
                        dropped_dup += 1
                        continue
                    buf.append({"query": rec["query"], "positive": rec["positive"], "negatives": rec["negatives"],
                                "source": name, "task_type": src["task_type"], "script": script_of(rec["query"]),
                                "origin": src.get("origin", "unknown"), "license": src.get("license", "unknown")})
                    kept += 1
                    if len(buf) >= shard_rows:
                        db.commit()
                        flush()
                if cap and kept >= cap:
                    break
        except Exception as e:
            log.error("source %s failed: %s", name, str(e)[:300])
            stats[name] = {"error": str(e)[:300], "kept": kept}
            continue
        db.commit()
        stats[name] = {"rows_read": seen_rows, "kept": kept, "dropped_filter": dropped_flt,
                       "dropped_benchmark_overlap": dropped_excl, "dropped_duplicate": dropped_dup,
                       "task_type": src["task_type"], "license": src.get("license")}
        log.info("%s: read %d, kept %d, filtered %d, benchmark-overlap %d, dup %d",
                 name, seen_rows, kept, dropped_flt, dropped_excl, dropped_dup)
        (workdir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
        done.add(name)
        done_file.write_text("\n".join(sorted(done)))
    flush()
    db.commit()
    (workdir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    if not dry_run:
        api.upload_file(path_or_fileobj=str(workdir / "stats.json"), path_in_repo="stats.json",
                        repo_id=repo, repo_type="dataset", commit_message="Update stats")
    log.info("TOTAL kept %d", sum(v.get("kept", 0) for v in stats.values()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Compile Nepali embedding pairs and upload to HF")
    ap.add_argument("--config", required=True)
    ap.add_argument("--workdir", default="data/retrieval_pairs")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--token", default=None)
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    run(cfg, a.dry_run, Path(a.workdir), a.token)


if __name__ == "__main__":
    main()

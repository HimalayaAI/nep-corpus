from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from nepali_corpus.core.utils.io import open_text
DEFAULT_RELATIVE_ROOTS = [
    "data/raw",
    "data/enriched",
    "data/cleaned",
    "data/dedup",
    "data/final",
    "data/hf",
    "datasets",
]


def get_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_roots(repo_root: Optional[Path] = None, roots: Optional[Iterable[Path]] = None) -> List[Path]:
    root = repo_root or get_repo_root()
    if roots is not None:
        return [Path(p) for p in roots]
    return [root / rel for rel in DEFAULT_RELATIVE_ROOTS]


def list_jsonl_files(
    repo_root: Optional[Path] = None,
    roots: Optional[Iterable[Path]] = None,
    include_gz: bool = False,
) -> List[str]:
    repo_root = repo_root or get_repo_root()
    jsonl_files: List[str] = []
    for root in _resolve_roots(repo_root, roots):
        if not root.exists():
            continue
        patterns = ["*.jsonl"]
        if include_gz:
            patterns.append("*.jsonl.gz")
        for pattern in patterns:
            for path in root.rglob(pattern):
                try:
                    rel = path.resolve().relative_to(repo_root.resolve())
                except Exception:
                    continue
                jsonl_files.append(rel.as_posix())
    return sorted(jsonl_files)


def make_table_name(relative_path: str) -> str:
    return f"file:{relative_path}"


def list_file_tables(repo_root: Optional[Path] = None, roots: Optional[Iterable[Path]] = None) -> List[str]:
    return [make_table_name(p) for p in list_jsonl_files(repo_root, roots, include_gz=True)]


def resolve_file_table(
    table_name: str,
    repo_root: Optional[Path] = None,
    roots: Optional[Iterable[Path]] = None,
) -> Optional[Path]:
    if not table_name.startswith("file:"):
        return None
    repo_root = repo_root or get_repo_root()
    rel = table_name[len("file:") :]
    path = (repo_root / rel).resolve()

    allowed_roots = _resolve_roots(repo_root, roots)
    allowed = False
    for root in allowed_roots:
        try:
            root_resolved = root.resolve()
        except Exception:
            continue
        if root_resolved in path.parents or root_resolved == path:
            allowed = True
            break

    if not allowed:
        return None
    if not path.exists():
        return None
    return path


def resolve_data_file(
    relative_path: str,
    repo_root: Optional[Path] = None,
    roots: Optional[Iterable[Path]] = None,
) -> Optional[Path]:
    repo_root = repo_root or get_repo_root()
    path = (repo_root / relative_path).resolve()
    allowed_roots = _resolve_roots(repo_root, roots)
    allowed = False
    for root in allowed_roots:
        try:
            root_resolved = root.resolve()
        except Exception:
            continue
        if root_resolved in path.parents or root_resolved == path:
            allowed = True
            break
    if not allowed or not path.exists():
        return None
    return path


def infer_columns_from_jsonl(path: Path, sample_size: int = 50) -> List[Dict[str, str]]:
    types: Dict[str, str] = {}
    with open_text(path, "rt") as f:
        for i, line in enumerate(f):
            if i >= sample_size:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            for key, value in obj.items():
                if key in types:
                    continue
                if isinstance(value, bool):
                    types[key] = "boolean"
                elif isinstance(value, (int, float)):
                    types[key] = "numeric"
                elif isinstance(value, (list, dict)):
                    types[key] = "json"
                elif value is None:
                    types[key] = "null"
                else:
                    types[key] = "text"
    return [{"name": k, "type": v} for k, v in types.items()]


def read_jsonl_page(path: Path, page: int, page_size: int) -> Tuple[List[dict], int]:
    start = (page - 1) * page_size
    end = start + page_size
    data: List[dict] = []
    total = 0

    with open_text(path, "rt") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if total >= start and total < end:
                data.append(obj if isinstance(obj, dict) else {"value": obj})
            total += 1

    return data, total


def search_jsonl(
    path: Path,
    search_term: str,
    page: int,
    page_size: int,
    columns: Optional[List[str]] = None,
) -> Tuple[List[dict], int]:
    start = (page - 1) * page_size
    end = start + page_size
    matches: List[dict] = []
    total = 0

    with open_text(path, "rt") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            haystack = ""
            if isinstance(obj, dict) and columns:
                parts = [str(obj.get(c, "")) for c in columns]
                haystack = " ".join(parts)
            else:
                haystack = json.dumps(obj, ensure_ascii=False)

            if search_term.lower() in haystack.lower():
                if total >= start and total < end:
                    matches.append(obj if isinstance(obj, dict) else {"value": obj})
                total += 1
            else:
                continue

    return matches, total

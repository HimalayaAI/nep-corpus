from __future__ import annotations

import gzip
import os
from pathlib import Path
from typing import IO


def ensure_parent_dir(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def maybe_gzip_path(path: str, gzip_output: bool) -> str:
    if gzip_output and not path.endswith(".gz"):
        return f"{path}.gz"
    return path


def open_text(path: str | Path, mode: str = "rt") -> IO[str]:
    path = str(path)
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


__all__ = ["ensure_parent_dir", "maybe_gzip_path", "open_text"]

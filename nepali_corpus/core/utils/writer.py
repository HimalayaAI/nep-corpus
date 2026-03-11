from __future__ import annotations

import json
import threading
from typing import Any

from .io import ensure_parent_dir, maybe_gzip_path, open_text


class JsonlWriter:
    def __init__(self, path: str, gzip_output: bool = False, append: bool = False):
        self.path = maybe_gzip_path(path, gzip_output)
        ensure_parent_dir(self.path)
        self._lock = threading.Lock()
        mode = "at" if append else "wt"
        self._fh = open_text(self.path, mode)
        self.count = 0

    def write(self, record: Any) -> None:
        if hasattr(record, "model_dump"):
            payload = record.model_dump()
        elif hasattr(record, "dict"):
            payload = record.dict()
        else:
            payload = record
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self._lock:
            self._fh.write(line)
            self.count += 1

    def flush(self) -> None:
        with self._lock:
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()


__all__ = ["JsonlWriter"]

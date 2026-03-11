from __future__ import annotations

import json
from typing import Iterable

from ..models import TrainingDocument
from .io import ensure_parent_dir, maybe_gzip_path, open_text


def export_jsonl(docs: Iterable[TrainingDocument], path: str, gzip_output: bool = False) -> int:
    path = maybe_gzip_path(path, gzip_output)
    ensure_parent_dir(path)
    count = 0
    with open_text(path, "wt") as f:
        for doc in docs:
            f.write(json.dumps(doc.model_dump(), ensure_ascii=False) + "\n")
            count += 1
    return count

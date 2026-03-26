"""Version helpers."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import tomllib


def _read_pyproject_version() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"][
        "version"
    ]


def _resolve_version() -> str:
    try:
        return version("likhit")
    except PackageNotFoundError:
        # Fall back to local project metadata when running from a source checkout.
        return _read_pyproject_version()


__version__ = _resolve_version()

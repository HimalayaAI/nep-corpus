"""Legacy Nepali font conversion helpers."""

from __future__ import annotations

import os
from typing import Callable

from likhit.errors import ExtractionError

_REGISTRY: dict[str, str] = {
    "preeti": "Preeti",
    "fontasy_himali": "FONTASY_HIMALI_TT",
    "fontasyhimali": "FONTASY_HIMALI_TT",
    "himali": "FONTASY_HIMALI_TT",
    "himalb": "FONTASY_HIMALI_TT",
    "kantipur": "Kantipur",
    "pcs nepali": "PCS NEPALI",
    "pcs_nepali": "PCS NEPALI",
    "pcsnepali": "PCS NEPALI",
    "sagarmatha": "Sagarmatha",
}

_mapper = None


def _match_font(font_name: str) -> str | None:
    base = font_name.split("+", 1)[-1] if "+" in font_name else font_name
    base = base.split(",")[0]
    base_lower = base.lower().strip()
    for key, map_key in _REGISTRY.items():
        if key in base_lower:
            return map_key
    return None


def _get_mapper():
    global _mapper
    if _mapper is not None:
        return _mapper

    try:
        import npttf2utf
        from npttf2utf.base.fontmapper import FontMapper
    except ModuleNotFoundError as exc:
        raise ExtractionError(
            "npttf2utf is required for legacy Nepali font conversion but is not installed"
        ) from exc

    map_json = os.path.join(os.path.dirname(npttf2utf.__file__), "map.json")
    _mapper = FontMapper(map_json)
    return _mapper


def get_converter(font_name: str) -> Callable[[str], str] | None:
    map_key = _match_font(font_name)
    if map_key is None:
        return None

    mapper = _get_mapper()

    def _convert(text: str) -> str:
        return mapper.map_to_unicode(text, from_font=map_key)

    return _convert


def is_legacy_font(font_name: str) -> bool:
    return _match_font(font_name) is not None

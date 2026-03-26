"""Font strategy classification for extraction."""

from __future__ import annotations

import logging

import fitz

from . import legacy_maps

logger = logging.getLogger(__name__)

_KNOWN_BROKEN_CMAP = {"kalimati"}


def classify_font(font_name: str, font_type: str) -> str:
    """Classify a PDF font into an extraction strategy."""

    del font_type

    base = font_name.split("+", 1)[-1] if "+" in font_name else font_name
    base_lower = base.lower().strip()

    if legacy_maps.is_legacy_font(font_name):
        return "legacy_remap"

    for name in _KNOWN_BROKEN_CMAP:
        if name in base_lower:
            return "broken_cmap"

    return "correct"


def scan_pdf_fonts(doc: fitz.Document) -> dict[str, str]:
    """Scan all PDF fonts and return a strategy per unique base font name."""

    font_strategies: dict[str, str] = {}

    for page_index in range(doc.page_count):
        page = doc[page_index]
        for font_info in page.get_fonts(full=True):
            _xref, _ext, font_type, name, _encoding = font_info[:5]
            base = name.split("+", 1)[-1] if "+" in name else name
            if base in font_strategies:
                continue
            strategy = classify_font(name, font_type)
            font_strategies[base] = strategy
            logger.info("Font '%s' (type=%s) -> %s", base, font_type, strategy)

    return font_strategies

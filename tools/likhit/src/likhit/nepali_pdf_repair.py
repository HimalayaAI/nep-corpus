"""Reusable Nepali PDF repair helpers for Markdown conversion."""

from __future__ import annotations

from collections import defaultdict
import re
from pathlib import Path

import fitz

from likhit.errors import ExtractionError, ValidationError
from likhit.extractors.base import TextFragment
from likhit.extractors.font_classifier import scan_pdf_fonts
from likhit.extractors.kalimati import (
    fix_kalimati_cmap,
    normalize_devanagari_spacing,
    reorder_devanagari,
)
from likhit.extractors.legacy_maps import get_converter
from likhit.extractors.tables import detect_page_tables, merge_continuation_tables
from likhit.handlers.content_blocks import build_content_blocks, table_to_plain_text
from likhit.models import TableBlock
from likhit.models.repair_types import RepairedBlock

_PDF_MIME_TYPES = ("application/pdf",)
_PDF_EXTENSIONS = {".pdf"}
_LIST_MARKER_PATTERN = re.compile(
    r"^(?P<marker>(?:[-*•])|(?:\d+[\.\)])|(?:[A-Za-z][\.\)]))\s+(?P<body>.+)$"
)


def needs_nepali_pdf_repair(source: bytes | str | Path) -> bool:
    """Return True when the PDF uses a known Nepali repair strategy."""

    doc = _open_pdf(source)
    try:
        return any(
            strategy in {"broken_cmap", "legacy_remap"}
            for strategy in scan_pdf_fonts(doc).values()
        )
    finally:
        doc.close()


def extract_repaired_text_blocks(source: bytes | str | Path) -> list[RepairedBlock]:
    """Extract ordered, repaired text blocks from a born-digital PDF."""

    doc = _open_pdf(source)
    try:
        font_strategies = scan_pdf_fonts(doc)
        has_broken_cmap = any(
            strategy == "broken_cmap" for strategy in font_strategies.values()
        )
        needs_reorder = False
        if has_broken_cmap:
            doc, needs_reorder = fix_kalimati_cmap(doc)

        fragments, tables = _extract_fragments_and_tables(
            doc,
            font_strategies,
            needs_reorder=needs_reorder,
        )

        if not fragments and not tables:
            raise ExtractionError(
                "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
            )

        blocks = build_content_blocks(
            fragments,
            merge_continuation_tables(tables),
            _build_paragraphs,
        )

        repaired_blocks: list[RepairedBlock] = []
        for order_index, block in enumerate(blocks):
            if isinstance(block, TableBlock):
                repaired_blocks.append(
                    RepairedBlock(
                        text=table_to_plain_text(block.table),
                        order_index=order_index,
                        page_number=block.table.page_number,
                        table=block.table,
                    )
                )
                continue

            heading_level = _detect_heading_level(block.text, order_index)
            list_marker = _detect_list_marker(block.text)
            repaired_blocks.append(
                RepairedBlock(
                    text=block.text.strip(),
                    order_index=order_index,
                    page_number=_page_number_for_paragraph(block.text, fragments),
                    heading_level=heading_level,
                    list_marker=list_marker,
                )
            )

        return [block for block in repaired_blocks if block.text.strip() or block.table]
    finally:
        doc.close()


def is_pdf_stream(stream_info: object) -> bool:
    """Return True when MarkItDown stream metadata looks like a PDF."""

    extension = getattr(stream_info, "extension", None)
    mimetype = getattr(stream_info, "mimetype", None)
    return (
        isinstance(extension, str)
        and extension.lower() in _PDF_EXTENSIONS
        or isinstance(mimetype, str)
        and any(mimetype.lower().startswith(prefix) for prefix in _PDF_MIME_TYPES)
    )


def _open_pdf(source: bytes | str | Path) -> fitz.Document:
    if isinstance(source, bytes):
        try:
            return fitz.open(stream=source, filetype="pdf")
        except Exception as exc:
            raise ExtractionError(
                "Unable to parse PDF. File may be corrupted or encrypted"
            ) from exc

    path = Path(source)
    if path.suffix.lower() != ".pdf":
        raise ValidationError("Unsupported file format. Please upload a PDF file")
    if not path.exists():
        raise ValidationError(f"File not found: {path}")

    try:
        return fitz.open(path)
    except Exception as exc:
        raise ExtractionError(
            "Unable to parse PDF. File may be corrupted or encrypted"
        ) from exc


def _extract_fragments_and_tables(
    doc: fitz.Document,
    font_strategies: dict[str, str],
    *,
    needs_reorder: bool,
) -> tuple[list[TextFragment], list]:
    fragments: list[TextFragment] = []
    tables = []
    table_index = 0

    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        lines_by_key: dict[
            tuple[int, int], list[tuple[float, float, float, float, str]]
        ] = defaultdict(list)

        for block_number, block in enumerate(page_dict["blocks"]):
            if "lines" not in block:
                continue
            for line_number, line in enumerate(block["lines"]):
                for span in line["spans"]:
                    text = _convert_span_text(
                        str(span["text"]),
                        str(span["font"]),
                        font_strategies,
                        needs_reorder=needs_reorder,
                    )
                    if not text.strip():
                        continue
                    x0, y0, x1, y1 = span["bbox"]
                    lines_by_key[(block_number, line_number)].append(
                        (
                            float(x0),
                            float(y0),
                            float(x1),
                            float(y1),
                            text,
                        )
                    )

        page_fragments: list[TextFragment] = []
        previous_y1: float | None = None
        for (block_number, line_number), line_words in sorted(
            lines_by_key.items(),
            key=lambda item: (
                round(min(piece[1] for piece in item[1]), 2),
                min(piece[0] for piece in item[1]),
            ),
        ):
            ordered_words = sorted(line_words, key=lambda piece: piece[0])
            line_text = "".join(piece[4] for piece in ordered_words)
            paragraph = _normalize_line_text(line_text)
            if not paragraph:
                previous_y1 = None
                continue

            x0 = min(piece[0] for piece in ordered_words)
            y0 = min(piece[1] for piece in ordered_words)
            x1 = max(piece[2] for piece in ordered_words)
            y1 = max(piece[3] for piece in ordered_words)
            gap_before = None if previous_y1 is None else y0 - previous_y1
            previous_y1 = y1

            page_fragments.append(
                TextFragment(
                    text=paragraph,
                    page_number=page_index + 1,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    block_number=block_number,
                    line_number=line_number,
                    gap_before=gap_before,
                )
            )

        fragments.extend(page_fragments)
        page_tables = detect_page_tables(page, page_fragments, table_index)
        tables.extend(page_tables)
        table_index += len(page_tables)

    return fragments, tables


def _convert_span_text(
    text: str,
    font_name: str,
    font_strategies: dict[str, str],
    *,
    needs_reorder: bool,
) -> str:
    base = font_name.split("+", 1)[-1] if "+" in font_name else font_name
    strategy = font_strategies.get(base, "correct")

    if strategy == "legacy_remap":
        converter = get_converter(font_name)
        if converter is not None:
            return converter(text)
        return text

    if strategy == "broken_cmap" and needs_reorder:
        text = normalize_devanagari_spacing(text)
        text = reorder_devanagari(text)
    return text


def _normalize_line_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized = re.sub(r"\s+([।,:;])", r"\1", normalized)
    return normalized


def _build_paragraphs(fragments: list[TextFragment]) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    previous_fragment: TextFragment | None = None

    for fragment in fragments:
        text = fragment.text.strip()
        if not text:
            continue

        if _starts_new_paragraph(previous_fragment, fragment, current):
            paragraphs.append(" ".join(current).strip())
            current = [text]
        else:
            current.append(text)
        previous_fragment = fragment

    if current:
        paragraphs.append(" ".join(current).strip())

    return [paragraph for paragraph in paragraphs if paragraph]


def _starts_new_paragraph(
    previous: TextFragment | None,
    current: TextFragment,
    current_parts: list[str],
) -> bool:
    if previous is None or not current_parts:
        return False
    if current.page_number != previous.page_number:
        return True
    if _detect_list_marker(current.text) is not None:
        return True
    if current.gap_before is None:
        return False
    line_height = max(previous.y1 - previous.y0, current.y1 - current.y0, 1.0)
    return current.gap_before >= max(6.0, line_height * 0.7)


def _detect_heading_level(text: str, order_index: int) -> int | None:
    stripped = text.strip()
    if not stripped or len(stripped) > 120:
        return None
    if _detect_list_marker(stripped) is not None:
        return None
    if stripped.endswith(("।", ".", "?", "!", ":")):
        return 1 if order_index == 0 else None
    if order_index == 0:
        return 1
    if len(stripped.split()) <= 10:
        return 2
    return None


def _detect_list_marker(text: str) -> str | None:
    match = _LIST_MARKER_PATTERN.match(text.strip())
    if match is None:
        return None
    return match.group("marker")


def _page_number_for_paragraph(
    paragraph_text: str,
    fragments: list[TextFragment],
) -> int:
    for fragment in fragments:
        if fragment.text and fragment.text in paragraph_text:
            return fragment.page_number
    return 1

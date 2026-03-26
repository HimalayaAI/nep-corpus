"""Font-based extraction for CIAA documents."""

from __future__ import annotations

from collections import defaultdict
import re
from pathlib import Path

import fitz

from likhit.errors import ExtractionError, ValidationError
from likhit.extractors.base import ExtractionStrategy, RawDocument, TextFragment
from likhit.extractors.font_classifier import scan_pdf_fonts
from likhit.extractors.kalimati import (
    fix_kalimati_cmap,
    normalize_devanagari_spacing,
    reorder_devanagari,
)
from likhit.extractors.legacy_maps import get_converter
from likhit.extractors.tables import detect_page_tables, merge_continuation_tables
from likhit.models import Table


PAGE_RANGE_PATTERN = re.compile(r"^\d+(?:-\d+)?$")
SPAN_GAP_THRESHOLD = 0.75


def parse_page_range(spec: str, total_pages: int) -> tuple[int, int]:
    """Parse a 1-based inclusive page range to 0-based bounds."""

    if not PAGE_RANGE_PATTERN.fullmatch(spec.strip()):
        raise ValidationError("Invalid page range format. Use format: '1-3' or '5'")

    if "-" in spec:
        start_text, end_text = spec.split("-", 1)
        start = int(start_text)
        end = int(end_text)
    else:
        start = end = int(spec)

    if start < 1 or end < 1 or end < start:
        raise ValidationError("Invalid page range format. Use format: '1-3' or '5'")

    if start > total_pages:
        raise ValidationError(
            f"Requested page range starts beyond document length ({total_pages} pages)"
        )

    end = min(end, total_pages)
    return start - 1, end - 1


def normalize_press_release_paragraph(text: str) -> str:
    text = text.strip()
    if not text:
        return ""

    normalized = text
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"\s+([।,:;])", r"\1", normalized)
    return normalized


def join_words_with_spacing(words: list[str]) -> str:
    """Reconstruct a line from extracted word tokens."""

    return " ".join(word.strip() for word in words if word.strip())


def join_spans_with_layout(
    spans: list[tuple[float, float, float, float, str]],
) -> str:
    """Reconstruct a line from positioned spans without forcing spaces inside words."""

    if not spans:
        return ""

    parts: list[str] = []
    previous_x1: float | None = None
    for x0, _y0, x1, _y1, text in spans:
        if not text:
            continue
        if (
            previous_x1 is not None
            and x0 - previous_x1 > SPAN_GAP_THRESHOLD
            and parts
            and not parts[-1].endswith((" ", "\t"))
            and not text.startswith((" ", "\t"))
        ):
            parts.append(" ")
        parts.append(text)
        previous_x1 = x1

    return "".join(parts)


def normalize_extracted_word(text: str) -> str:
    """Normalize a single extracted token without touching inter-word spacing."""

    normalized = reorder_devanagari(text)
    normalized = normalize_devanagari_spacing(normalized)
    return normalized.strip()


class FontBasedStrategy(ExtractionStrategy):
    """Extract text from CIAA PDFs using PyMuPDF blocks."""

    def extract_text(self, file_path: str, pages: str | None = None) -> RawDocument:
        return self._extract_raw_document(file_path, pages=pages)

    def extract_tables(self, file_path: str) -> list[Table]:
        return self._extract_raw_document(file_path).tables

    def _extract_raw_document(
        self,
        file_path: str,
        pages: str | None = None,
    ) -> RawDocument:
        path = Path(file_path)
        if path.suffix.lower() != ".pdf":
            raise ValidationError("Unsupported file format. Please upload a PDF file")
        if not path.exists():
            raise ValidationError(f"File not found: {file_path}")

        try:
            doc = fitz.open(path)
        except Exception as exc:
            raise ExtractionError(
                "Unable to parse PDF. File may be corrupted or encrypted"
            ) from exc

        try:
            page_start, page_end = 0, doc.page_count - 1
            if pages:
                page_start, page_end = parse_page_range(pages, doc.page_count)

            font_strategies = scan_pdf_fonts(doc)
            has_broken_cmap = any(
                strategy == "broken_cmap" for strategy in font_strategies.values()
            )
            needs_reorder = False
            if has_broken_cmap:
                doc, needs_reorder = fix_kalimati_cmap(doc)
            paragraphs: list[str] = []
            fragments: list[TextFragment] = []
            tables: list[Table] = []
            table_index = 0

            for page_index in range(page_start, page_end + 1):
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
                            text = self._convert_span_text(
                                str(span["text"]),
                                str(span["font"]),
                                font_strategies,
                                needs_reorder,
                            )
                            if not text:
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
                    line_text = join_spans_with_layout(ordered_words)
                    paragraph = normalize_press_release_paragraph(line_text)
                    if not paragraph:
                        previous_y1 = None
                        continue

                    x0 = min(piece[0] for piece in ordered_words)
                    y0 = min(piece[1] for piece in ordered_words)
                    x1 = max(piece[2] for piece in ordered_words)
                    y1 = max(piece[3] for piece in ordered_words)
                    gap_before = None
                    if previous_y1 is not None:
                        gap_before = y0 - previous_y1
                    previous_y1 = y1

                    fragment = TextFragment(
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
                    paragraphs.append(paragraph)
                    page_fragments.append(fragment)

                fragments.extend(page_fragments)
                page_tables = detect_page_tables(page, page_fragments, table_index)
                tables.extend(page_tables)
                table_index += len(page_tables)

            raw_text = "\n\n".join(paragraphs).strip()
            if not raw_text:
                raise ExtractionError("No text content found in document")

            return RawDocument(
                paragraphs=paragraphs,
                raw_text=raw_text,
                fragments=fragments,
                tables=merge_continuation_tables(tables),
            )
        except (ExtractionError, ValidationError):
            raise
        except Exception as exc:
            raise ExtractionError(
                f"Failed to extract text from PDF: {path.name}"
            ) from exc
        finally:
            doc.close()

    def _convert_span_text(
        self,
        text: str,
        font_name: str,
        font_strategies: dict[str, str],
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
            text = reorder_devanagari(text)
            text = normalize_devanagari_spacing(text)
        return text

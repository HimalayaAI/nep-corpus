"""Kanun Patrika document handler."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

from likhit.errors import ExtractionError
from likhit.extractors.base import ExtractionStrategy, RawDocument, TextFragment
from likhit.extractors.docx_based import DocxBasedStrategy
from likhit.extractors.font_based import FontBasedStrategy
from likhit.handlers.base import DocumentTypeHandler
from likhit.handlers.content_blocks import blocks_to_text, build_content_blocks
from likhit.models import DocumentType, ExtractionResult, ParagraphBlock, Section
from likhit.models.types import ContentBlock

_NOISE_ONLY_PATTERN = re.compile(r"^(?:\d+|[A-Za-z+\-^&*/\\|=()]+)$")
_HEADER_Y_MAX = 80.0
_COLUMN_GUTTER = 20.0


def _clean_paragraph(text: str) -> str:
    cleaned = text.replace("\ufffd", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class KanunPatrikaHandler(DocumentTypeHandler):
    """Handle Kanun Patrika journal-style documents."""

    def __init__(self) -> None:
        self._strategy = FontBasedStrategy()
        self._docx_strategy = DocxBasedStrategy()

    def get_extraction_strategy(self) -> FontBasedStrategy:
        return self._strategy

    def get_extraction_strategy_for_file(self, file_path: str) -> ExtractionStrategy:
        """Route to appropriate strategy based on file extension.

        Note: Legacy .doc files are not supported for Kanun Patrika.
        """
        suffix = Path(file_path).suffix.lower()
        if suffix == ".docx":
            return self._docx_strategy
        if suffix == ".doc":
            raise ExtractionError(
                "Legacy .doc format is not supported for Kanun Patrika. "
                "Please convert to .docx or PDF format."
            )
        return self._strategy

    def build_result(
        self, raw_document: RawDocument, metadata: dict[str, str | None]
    ) -> ExtractionResult:
        blocks = self._build_blocks(raw_document)
        paragraphs = [
            block.text for block in blocks if isinstance(block, ParagraphBlock)
        ]
        if not paragraphs:
            raise ExtractionError("No text content found in document")

        title = metadata.get("title") or self._extract_title(paragraphs)
        body = blocks_to_text(blocks).strip()
        section = Section(heading=None, body=body, level=1, blocks=blocks)
        return ExtractionResult(
            title=title,
            doc_type=DocumentType.KANUN_PATRIKA,
            source_url=metadata.get("source_url"),
            publication_date=metadata.get("publication_date"),
            sections=[section],
            metadata={"source_name": "Kanun Patrika"},
        )

    def _extract_title(self, paragraphs: list[str]) -> str:
        for paragraph in paragraphs:
            if not self._is_noise_only(paragraph):
                return paragraph
        return "कानून पत्रिका"

    def _build_blocks(self, raw_document: RawDocument) -> list[ContentBlock]:
        if not raw_document.fragments:
            return [
                ParagraphBlock(text=cleaned)
                for paragraph in raw_document.paragraphs
                if (cleaned := _clean_paragraph(paragraph))
            ]

        ordered_fragments: list[TextFragment] = []
        by_page: dict[int, list[TextFragment]] = defaultdict(list)
        for fragment in raw_document.fragments:
            cleaned = _clean_paragraph(fragment.text)
            if not cleaned:
                continue
            by_page[fragment.page_number].append(fragment)

        for page_number in sorted(by_page):
            page_fragments = by_page[page_number]
            ordered_fragments.extend(self._order_page_fragments(page_fragments))

        return build_content_blocks(
            ordered_fragments,
            raw_document.tables,
            self._merge_fragments_to_paragraphs,
        )

    def _order_page_fragments(
        self, fragments: list[TextFragment]
    ) -> list[TextFragment]:
        if not fragments:
            return []

        ordered = sorted(fragments, key=lambda fragment: (fragment.y0, fragment.x0))
        header = [fragment for fragment in ordered if fragment.y0 <= _HEADER_Y_MAX]
        body = [fragment for fragment in ordered if fragment.y0 > _HEADER_Y_MAX]
        if not body:
            return header

        page_left = min(fragment.x0 for fragment in body)
        page_right = max(fragment.x1 for fragment in body)
        split_x = (page_left + page_right) / 2

        left: list[TextFragment] = []
        right: list[TextFragment] = []
        centered: list[TextFragment] = []

        for fragment in body:
            center_x = (fragment.x0 + fragment.x1) / 2
            if center_x <= split_x - _COLUMN_GUTTER:
                left.append(fragment)
            elif center_x >= split_x + _COLUMN_GUTTER:
                right.append(fragment)
            else:
                centered.append(fragment)

        if not left or not right:
            return header + body

        left = sorted(left, key=lambda fragment: (fragment.y0, fragment.x0))
        right = sorted(right, key=lambda fragment: (fragment.y0, fragment.x0))
        centered = sorted(centered, key=lambda fragment: (fragment.y0, fragment.x0))
        return header + left + right + centered

    def _merge_fragments_to_paragraphs(
        self, fragments: list[TextFragment]
    ) -> list[str]:
        if not fragments:
            return []

        typical_line_height = min(
            (
                sorted(fragment.y1 - fragment.y0 for fragment in fragments)[
                    len(fragments) // 2
                ]
            ),
            24.0,
        )
        line_merge_threshold = max(1.5, typical_line_height * 0.18)
        paragraph_gap_threshold = max(8.0, typical_line_height * 0.7)

        merged_lines: list[tuple[float, float, str]] = []
        current_line: list[TextFragment] = []

        def flush_line() -> None:
            if not current_line:
                return
            ordered_line = sorted(current_line, key=lambda fragment: fragment.x0)
            y0 = min(fragment.y0 for fragment in ordered_line)
            y1 = max(fragment.y1 for fragment in ordered_line)
            text = " ".join(
                _clean_paragraph(fragment.text)
                for fragment in ordered_line
                if _clean_paragraph(fragment.text)
            ).strip()
            if text:
                merged_lines.append((y0, y1, text))
            current_line.clear()

        for fragment in fragments:
            if not current_line:
                current_line.append(fragment)
                continue

            current_y0 = min(item.y0 for item in current_line)
            if abs(fragment.y0 - current_y0) <= line_merge_threshold:
                current_line.append(fragment)
                continue

            flush_line()
            current_line.append(fragment)

        flush_line()

        paragraphs: list[str] = []
        current_paragraph: list[str] = []
        previous_y1: float | None = None

        def flush_paragraph() -> None:
            if current_paragraph:
                paragraphs.append("\n".join(current_paragraph).strip())
                current_paragraph.clear()

        for y0, y1, text in merged_lines:
            if previous_y1 is not None:
                gap = y0 - previous_y1
                if gap > paragraph_gap_threshold or gap < -line_merge_threshold:
                    flush_paragraph()
            current_paragraph.append(text)
            previous_y1 = y1

        flush_paragraph()
        return paragraphs

    def _is_noise_only(self, text: str) -> bool:
        compact = text.replace(" ", "")
        if _NOISE_ONLY_PATTERN.fullmatch(compact):
            return True
        return False

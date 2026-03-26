"""Public conversion entry points."""

from __future__ import annotations

from pathlib import Path
import re

from likhit.errors import ExtractionError, ValidationError
from likhit.handlers import CIAAPressReleaseHandler, KanunPatrikaHandler
from likhit.markitdown_integration import convert_pdf_to_markdown
from likhit.models import DocumentType, ExtractionResult
from likhit.renderers import MarkdownRenderer
from likhit.extractors.font_based import FontBasedStrategy

_KANUN_MARKERS = (
    "नेपालकानूनपत्रिका",
    "नेपालकानुनपत्रिका",
    "निर्णय नं",
    "ने.का.प.",
)
_CIAA_MARKERS = (
    "अख्तियार दुरुपयोग अनुसन्धान आयोग",
    "प्रेस विज्ञ",
    "प्रेस मिज्ञ",
    "विषय:",
    "मिषय:",
    "आयोगकोनिर्णय",
    "अनुसन्धानबाट पुष्टि भएको",
)


def _metadata_from_options(
    title: str | None,
    publication_date: str | None,
    source_url: str | None,
) -> dict[str, str | None]:
    return {
        "title": title,
        "publication_date": publication_date,
        "source_url": source_url,
    }


def _resolve_handler(
    doc_type: DocumentType,
) -> CIAAPressReleaseHandler | KanunPatrikaHandler:
    if doc_type is DocumentType.CIAA_PRESS_RELEASE:
        return CIAAPressReleaseHandler()
    if doc_type is DocumentType.KANUN_PATRIKA:
        return KanunPatrikaHandler()
    raise ValidationError(f"Unsupported document type: {doc_type.value}")


def _normalize_detection_text(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _detect_document_type(raw_text: str) -> DocumentType | None:
    normalized = _normalize_detection_text(raw_text)

    if any(
        _normalize_detection_text(marker) in normalized for marker in _KANUN_MARKERS
    ):
        return DocumentType.KANUN_PATRIKA
    if any(_normalize_detection_text(marker) in normalized for marker in _CIAA_MARKERS):
        return DocumentType.CIAA_PRESS_RELEASE
    return None


def _render_markdown_without_frontmatter(result: ExtractionResult) -> str:
    markdown = MarkdownRenderer().render(result).lstrip()
    if not markdown.startswith("---\n"):
        return markdown.strip()

    parts = markdown.split("\n---\n", 1)
    if len(parts) != 2:
        return markdown.strip()
    return parts[1].strip()


def _convert_with_detected_structure(file_path: str) -> str | None:
    strategy = FontBasedStrategy()
    try:
        raw_document = strategy.extract_text(file_path)
    except ExtractionError as exc:
        if str(exc) == "No text content found in document":
            return None
        raise
    doc_type = _detect_document_type(raw_document.raw_text)
    if doc_type is None:
        return None

    handler = _resolve_handler(doc_type)
    result = handler.build_result(
        raw_document,
        _metadata_from_options(None, None, None),
    )
    return _render_markdown_without_frontmatter(result)


def convert(file_path: str) -> str:
    """Convert a document (PDF, DOCX, or DOC) to Markdown.

    For PDFs, attempts structure-aware extraction first, then falls back to MarkItDown.
    For DOCX/DOC, extracts text and auto-detects document type (CIAA, Kanun Patrika, etc.).
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    # Handle DOCX/DOC files
    if suffix in {".docx", ".doc"}:
        # First, extract raw text using a temporary handler to get the strategy
        # We use CIAA handler initially just to get the extraction strategy
        temp_handler = CIAAPressReleaseHandler()
        strategy = temp_handler.get_extraction_strategy_for_file(file_path)
        raw_document = strategy.extract_text(file_path)

        # Detect document type from content (same logic as PDFs)
        doc_type = _detect_document_type(raw_document.raw_text)

        if doc_type is not None:
            # Use the detected handler
            handler = _resolve_handler(doc_type)
            # Re-get the strategy from the correct handler (handles DOC rejection for Kanun Patrika)
            try:
                strategy = handler.get_extraction_strategy_for_file(file_path)
                # Re-extract if needed (in case handler has different logic)
                raw_document = strategy.extract_text(file_path)
            except ExtractionError:
                # If the detected handler rejects the file (e.g., Kanun Patrika rejects DOC),
                # fall back to generic text rendering
                return raw_document.raw_text

            result = handler.build_result(
                raw_document,
                _metadata_from_options(None, None, None),
            )
            return _render_markdown_without_frontmatter(result)
        else:
            # No known layout detected, return plain text as markdown
            return raw_document.raw_text

    # Handle PDF files
    if suffix == ".pdf":
        structured_markdown = _convert_with_detected_structure(file_path)
        if structured_markdown is not None:
            return structured_markdown
        return convert_pdf_to_markdown(file_path)

    # Unsupported format
    raise ValidationError(
        f"Unsupported input format: {suffix}. Supported formats: .pdf, .docx, .doc"
    )


def derive_convert_output_name(source_path: str, existing: set[str]) -> str:
    base_name = Path(source_path).stem or "document"
    candidate = f"{base_name}.md"
    counter = 2
    while candidate in existing:
        candidate = f"{base_name}-{counter}.md"
        counter += 1
    existing.add(candidate)
    return candidate


def convert_many(file_paths: list[str]) -> list[tuple[str, str]]:
    return [(file_path, convert(file_path)) for file_path in file_paths]

"""MarkItDown integration for the public conversion path."""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from markitdown import (
    DocumentConverter,
    DocumentConverterResult,
    MarkItDown,
    StreamInfo,
)
from markitdown.converters._pdf_converter import PdfConverter

from likhit.errors import ExtractionError, ValidationError
from likhit.markdown_assembly import assemble_markdown, derive_markdown_title
from likhit.nepali_pdf_repair import (
    extract_repaired_text_blocks,
    is_pdf_stream,
    needs_nepali_pdf_repair,
)


class LikhitPdfConverter(DocumentConverter):
    """Intercept PDFs that need Nepali repair before Markdown conversion."""

    def __init__(self) -> None:
        self._fallback = PdfConverter()

    def accepts(
        self,
        file_stream: io.BufferedIOBase,
        stream_info: StreamInfo,
        **kwargs: object,
    ) -> bool:
        del file_stream, kwargs
        return is_pdf_stream(stream_info)

    def convert(
        self,
        file_stream: io.BufferedIOBase,
        stream_info: StreamInfo,
        **kwargs: object,
    ) -> DocumentConverterResult:
        pdf_bytes = file_stream.read()
        if not pdf_bytes:
            raise ExtractionError(
                "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
            )

        if not needs_nepali_pdf_repair(pdf_bytes):
            return self._fallback.convert(io.BytesIO(pdf_bytes), stream_info, **kwargs)

        blocks = extract_repaired_text_blocks(pdf_bytes)
        markdown = assemble_markdown(blocks)
        if not markdown.strip():
            raise ExtractionError(
                "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
            )
        return DocumentConverterResult(
            markdown=markdown,
            title=derive_markdown_title(blocks),
        )


@lru_cache(maxsize=1)
def get_markitdown() -> MarkItDown:
    """Create the shared MarkItDown instance used by convert()."""

    md = MarkItDown()
    md.register_converter(LikhitPdfConverter(), priority=-1)
    return md


def convert_pdf_to_markdown(file_path: str) -> str:
    """Convert a PDF file to Markdown with Likhit's repair-aware path."""

    path = Path(file_path)
    if path.suffix.lower() != ".pdf":
        raise ValidationError(
            "Unsupported input format for convert. Only born-digital PDF files are supported."
        )
    if not path.exists():
        raise ValidationError(f"File not found: {file_path}")

    result = get_markitdown().convert(file_path)
    markdown = result.text_content.strip()
    if not markdown:
        raise ExtractionError(
            "No extractable text found in PDF. Scanned or image-only PDFs are not supported."
        )
    return markdown

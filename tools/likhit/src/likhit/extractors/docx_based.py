"""Simple text extraction from DOCX and DOC files."""

from __future__ import annotations

import shutil
import subprocess

from markitdown import MarkItDown

from likhit.errors import ExtractionError
from likhit.extractors.base import ExtractionStrategy, RawDocument, TextFragment


class DocxBasedStrategy(ExtractionStrategy):
    """Extract plain text from DOCX and DOC files."""

    def __init__(self):
        self._markitdown = MarkItDown()

    def extract_text(self, file_path: str, pages: str | None = None) -> RawDocument:
        """Extract text from DOCX or DOC file.

        Args:
            file_path: Path to the DOCX or DOC file
            pages: Ignored for DOCX/DOC files (no page concept)

        Returns:
            RawDocument with extracted text fragments

        Raises:
            ExtractionError: If extraction fails or file format is unsupported
        """
        from pathlib import Path

        path = Path(file_path)
        suffix = path.suffix.lower()

        if suffix == ".docx":
            text = self._extract_docx(file_path)
        elif suffix == ".doc":
            text = self._extract_doc(file_path)
        else:
            raise ExtractionError(
                f"Unsupported file format: {suffix}. Only .docx and .doc are supported."
            )

        if not text or not text.strip():
            raise ExtractionError(
                "No extractable text found in document. The file may be empty or corrupted."
            )

        # Split into paragraphs and create fragments
        fragments = self._create_fragments(text)
        paragraphs = [f.text for f in fragments]

        return RawDocument(
            fragments=fragments,
            raw_text=text,
            paragraphs=paragraphs,
        )

    def extract_tables(self, file_path: str) -> list:
        """Extract tables from DOCX or DOC file.

        Note: Simple text extraction doesn't preserve table structure.
        Returns empty list as tables are extracted as plain text.
        """
        return []

    def _extract_docx(self, file_path: str) -> str:
        """Extract plain text from DOCX file using MarkItDown."""
        try:
            # MarkItDown converts DOCX to markdown, we extract the text content
            result = self._markitdown.convert(file_path)
            text = result.text_content
            return text if text else ""
        except Exception as e:
            raise ExtractionError(f"Failed to extract text from DOCX: {e}") from e

    def _extract_doc(self, file_path: str) -> str:
        """Extract plain text from legacy DOC file using antiword.

        The primary path uses pyantiword. If pyantiword's bundled antiword binary
        is incompatible with the current platform, we fall back to other
        locally available extractors.
        """
        try:
            # pyantiword.extract_text_with_antiword() takes a file path
            from pyantiword.antiword_wrapper import extract_text_with_antiword

            text = extract_text_with_antiword(file_path)
            return text if text else ""
        except Exception as e:
            fallback_text = self._extract_doc_with_system_antiword(file_path)
            if fallback_text is not None:
                return fallback_text

            fallback_text = self._extract_doc_with_textutil(file_path)
            if fallback_text is not None:
                return fallback_text

            err = str(e)
            if "Win32" in err or "WinError" in err:
                raise ExtractionError(
                    "Failed to extract text from DOC: pyantiword is not compatible with Windows. "
                    "Install antiword separately and ensure it is on PATH, or convert DOC to DOCX first. "
                    f"Original error: {e}"
                ) from e
            if "Exec format error" in err:
                raise ExtractionError(
                    "Failed to extract text from DOC: pyantiword bundled binary is not compatible with this OS/architecture. "
                    "Install antiword in your system PATH (for macOS: brew install antiword). "
                    f"Original error: {e}"
                ) from e
            raise ExtractionError(f"Failed to extract text from DOC: {e}") from e

    def _extract_doc_with_system_antiword(self, file_path: str) -> str | None:
        """Try extracting DOC text with a system antiword executable."""
        antiword_bin = shutil.which("antiword")
        if not antiword_bin:
            return None
        try:
            result = subprocess.run(
                [antiword_bin, file_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",  # Replace invalid UTF-8 with replacement character
                check=True,
            )
            text = result.stdout
            return text if text else ""
        except Exception:
            return None

    def _extract_doc_with_textutil(self, file_path: str) -> str | None:
        """Try extracting DOC text via macOS textutil when available."""
        textutil_bin = shutil.which("textutil")
        if not textutil_bin:
            return None
        try:
            result = subprocess.run(
                [textutil_bin, "-convert", "txt", "-stdout", file_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",  # Replace invalid UTF-8 with replacement character
                check=True,
            )
            text = result.stdout
            return text if text else ""
        except Exception:
            return None

    def _create_fragments(self, text: str) -> list[TextFragment]:
        """Split text into paragraph fragments with sequential positioning."""
        fragments = []
        paragraphs = text.split("\n")

        for idx, para in enumerate(paragraphs):
            para = para.strip()
            if para:  # Skip empty paragraphs
                fragments.append(
                    TextFragment(
                        text=para,
                        page_number=0,  # No page concept in DOCX/DOC
                        x0=0.0,
                        y0=float(idx * 20),  # Simulate vertical positioning
                        x1=100.0,
                        y1=float(idx * 20 + 15),
                        block_number=idx,
                        line_number=idx,
                        gap_before=None,
                    )
                )

        return fragments

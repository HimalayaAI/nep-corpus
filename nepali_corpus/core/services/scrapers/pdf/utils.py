from __future__ import annotations

import logging
from typing import List

try:
    import fitz  # PyMuPDF
    HAS_PYMUPDF = True
except Exception:
    HAS_PYMUPDF = False

logger = logging.getLogger(__name__)

def _extract_text_from_pdf(pdf_bytes: bytes) -> str:
    if not HAS_PYMUPDF:
        raise RuntimeError("PyMuPDF (fitz) is required for PDF extraction")
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        pages: List[str] = []
        for page in doc:
            pages.append(page.get_text("text"))
        doc.close()
        return "\n\n".join(pages)
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        return ""

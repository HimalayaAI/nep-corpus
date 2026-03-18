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
            
        extracted_text = "\n\n".join(pages).strip()
        
        # Fallback to OCR if:
        # 1. Text is too short (likely scanned)
        # 2. Text has very low Devanagari ratio (likely bad encoding/junk)
        from nepali_corpus.core.utils.normalize import devanagari_ratio
        ratio = devanagari_ratio(extracted_text)
        
        if len(extracted_text) < 200 or ratio < 0.2:
            try:
                import pytesseract
                from PIL import Image, ImageOps
                ocr_pages = []
                for page in doc:
                    pix = page.get_pixmap(dpi=300) # Increased to 300 dpi for better OCR
                    mode = "RGBA" if pix.alpha else "RGB"
                    img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
                    
                    # Pre-process: Grayscale + Auto-contrast
                    img = ImageOps.grayscale(img)
                    img = ImageOps.autocontrast(img)
                    
                    ocr_text = pytesseract.image_to_string(img, lang="nep+eng")
                    ocr_pages.append(ocr_text)
                
                ocr_extracted = "\n\n".join(ocr_pages).strip()
                ocr_ratio = devanagari_ratio(ocr_extracted)
                
                # Use OCR if it's longer OR if it has much better Devanagari density
                if len(ocr_extracted) > 100 and (len(ocr_extracted) > len(extracted_text) or ocr_ratio > ratio + 0.3):
                    extracted_text = ocr_extracted
            except ImportError:
                logger.debug("pytesseract or PIL not installed, skipping OCR fallback for PDF.")
            except Exception as e:
                logger.debug(f"PDF OCR extraction failed: {e}")
                
        doc.close()
        return extracted_text
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        return ""

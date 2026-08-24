from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import sys
import time
import urllib3
from typing import Optional, Tuple
from urllib.parse import urlparse, unquote

import requests
from bs4 import BeautifulSoup

from nepali_corpus.core.services.scrapers.pdf.utils import HAS_PYMUPDF, _extract_text_from_pdf
from nepali_corpus.core.utils.content_types import identify_content_type
from nepali_corpus.core.utils.scrapling_fetcher import HAS_SCRAPLING, is_likely_blocked, stealth_fetch
from .normalize import devanagari_ratio
from .boilerplate import clean_extracted_text

logger = logging.getLogger(__name__)

# ── Global Noise Selectors (Aggressive Sanitization) ───────────────────

GLOBAL_NOISE_SELECTORS = [
    # Splash ads and overlays
    "#ok-splash-wrapper", ".td-modal-image", ".pop-up-wrapper",
    ".modal-backdrop", ".interstitial-ad", ".overlay-container",
    # Social sidebars and floating widgets
    ".a2a_kit", ".social-share-fixed", ".floating-social",
    # Sticky headers/footers often caught in density
    ".sticky-bottom-ad", ".top-banner-ad",
    # Common news junk
    ".related-links", ".trending-news-sidebar", ".newsletter-popup",
]

# ── Expanded CSS selectors for Nepali news themes ───────────────────────

CONTENT_SELECTORS = [
    "article",
    "main",
    ".content-inner",
    ".post-content",
    ".article-content",
    "#content",
    ".entry-content",
    # WordPress themes common in Nepal
    ".td-post-content",
    ".jeg_inner_content",
    ".entry-inner",
    ".single-post-content",
    # Nepali news site specific
    ".news-detail-body",
    ".detail-content",
    ".news-content",
    ".story-body",
    ".blog-content",
    "#main-content",
    ".article-body",
    ".news-body",
    # Additional patterns
    ".post-body",
    ".content-area",
    ".main-content",
    ".page-content",
    ".single-content",
    "[itemprop='articleBody']",
    ".post_content",
    ".story-content",
]

# Elements to remove before extraction
BOILERPLATE_TAGS = [
    "script", "style", "noscript", "header", "footer",
    "nav", "aside", "form", "button", "iframe", "svg",
]

BOILERPLATE_SELECTORS = [
    "#sidebar", ".sidebar", "#footer", ".footer", ".nav", ".menu", ".ads",
    ".top-bar", ".top-header", ".language-switcher", ".social-links",
    ".breadcrumb", ".breadcrumb-wrapper", ".search-form", ".print-btn",
    ".share-links", ".related-posts", "#comments", ".pagination",
    ".download-section", ".contact-info", ".map-container", ".location-list",
    ".widget", ".sidebar-widget", ".footer-widget", ".hidden-print",
    # Additional
    ".advertisement", ".ad-container", ".banner", ".cookie-notice",
    ".newsletter-signup", ".popup", ".modal", ".overlay",
]

def _cache_path(cache_dir: str, url: str, ext: str = ".html") -> str:
    h = hashlib.md5(url.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, f"{h}{ext}")

def fetch_content(url: str, cache_dir: str, timeout: int = 30, delay: float = 0.5) -> Tuple[Optional[bytes], str]:
    """Fetches url content and returns (bytes, content_type). Downloads PDFs and HTML.

    Falls back to Scrapling's StealthFetcher when the plain requests response
    looks bot-blocked (non-200, very short body, or Cloudflare challenge page).
    The fallback is a no-op when Scrapling is not installed.
    """
    os.makedirs(cache_dir, exist_ok=True)

    # Check cache first for html or pdf
    html_path = _cache_path(cache_dir, url, ".html")
    pdf_path = _cache_path(cache_dir, url, ".pdf")
    if os.path.exists(html_path):
        with open(html_path, "rb") as f:
            return f.read(), "text/html"
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            return f.read(), "application/pdf"

    time.sleep(delay)
    # Unquote URL to avoid double-encoding by requests
    fetch_url = unquote(url)

    data: Optional[bytes] = None
    c_type: str = "text/html"

    try:
        r = requests.get(
            fetch_url,
            timeout=timeout,
            headers={"User-Agent": "NepaliCorpusBot/1.0 (+https://himalaya.ai)"},
            stream=True,
            verify=True,
        )

        plain_ok = r.status_code == 200
        content_type = r.headers.get("Content-Type", "").lower()
        MAX_SIZE = 50 * 1024 * 1024  # 50MB limit for all downloads

        if plain_ok:
            chunks = []
            bytes_read = 0
            for chunk in r.iter_content(chunk_size=8192):
                bytes_read += len(chunk)
                if bytes_read > MAX_SIZE:
                    logger.warning(f"Response too large, truncating: {url}")
                    break
                chunks.append(chunk)
            data = b"".join(chunks)

        if "application/pdf" in content_type:
            c_type = "application/pdf"
        else:
            c_type = "text/html"

        # Stealth fallback: retry with StealthFetcher if plain request is blocked
        if c_type == "text/html" and (not plain_ok or is_likely_blocked(data)):
            if HAS_SCRAPLING:
                logger.info("Plain fetch blocked/empty for %s — retrying with StealthFetcher", url)
                stealth_data = stealth_fetch(url, timeout=timeout)
                if stealth_data and not is_likely_blocked(stealth_data):
                    data = stealth_data
                    logger.info("StealthFetcher succeeded: %d bytes from %s", len(data), url)
                else:
                    logger.warning("StealthFetcher also failed/blocked for %s", url)
            else:
                if not plain_ok:
                    logger.warning(f"Failed to fetch {url}: HTTP {r.status_code}")
                return None, ""

        if data is None:
            return None, ""

        # Write to cache
        path = pdf_path if c_type == "application/pdf" else html_path
        with open(path, "wb") as f:
            f.write(data)
        return data, c_type

    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return None, ""

def _detect_encoding(data: bytes) -> str:
    """Detect encoding using charset-normalizer if available, fallback to utf-8."""
    try:
        from charset_normalizer import from_bytes
        result = from_bytes(data).best()
        if result and result.encoding:
            return result.encoding
    except ImportError:
        pass
    except Exception:
        pass
    return "utf-8"

def extract_text(
    data: bytes,
    content_type: str,
    url: Optional[str] = None,
    use_trafilatura: bool = True,
    ocr_enabled: bool = True,
    pdf_enabled: bool = True,
) -> str:
    if not data:
        return ""

    # Refine content type if needed using URL and sniff
    refined_type = identify_content_type(url or "", data)

    # If the provided content_type header is robust (like pdf), keep it, otherwise use refined
    if "pdf" in content_type.lower():
        ctype = "pdf"
    elif "json" in content_type.lower():
        ctype = "json"
    elif "xml" in content_type.lower():
        ctype = "xml"
    elif "csv" in content_type.lower():
        ctype = "csv"
    else:
        ctype = refined_type

    if ctype == "pdf":
        if not HAS_PYMUPDF:
            logger.warning(f"Skipping PDF {url} because PyMuPDF is not installed")
            return ""
        try:
            return _extract_text_from_pdf(data).strip()
        except Exception as e:
            logger.warning(f"Failed to extract PDF {url}: {e}")
            return ""

    if ctype == "json":
        try:
            import json
            obj = json.loads(data)
            return json.dumps(obj, indent=2, ensure_ascii=False)
        except Exception:
            pass

    if ctype == "csv":
        try:
            import csv
            import io
            text_data = data.decode("utf-8", errors="ignore")
            reader = csv.reader(io.StringIO(text_data))
            rows = [" ".join(row) for row in reader]
            return "\n".join(rows)
        except Exception:
            pass

    if ctype == "xml":
        try:
            soup = BeautifulSoup(data, "xml")
            return soup.get_text("\n")
        except Exception:
            pass

    # Default: treat as HTML
    # Parse HTML exactly ONCE and reuse across all candidate extractors.

    encoding = _detect_encoding(data)
    try:
        html = data.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        try:
            html = data.decode("utf-8")
        except UnicodeDecodeError:
            html = data.decode("utf-8", errors="ignore")

    # Build a single pre-sanitised soup for CSS-selector extraction
    try:
        shared_soup = (
            BeautifulSoup(html, "lxml")
            if "lxml" in sys.modules
            else BeautifulSoup(html, "html.parser")
        )
    except Exception:
        shared_soup = BeautifulSoup(html, "html.parser")

    # Apply global noise removal once on the shared soup
    for selector in GLOBAL_NOISE_SELECTORS:
        for noise in shared_soup.select(selector):
            noise.decompose()
    for tag in shared_soup(BOILERPLATE_TAGS):
        tag.extract()
    for selection in shared_soup.select(", ".join(BOILERPLATE_SELECTORS)):
        selection.extract()
    for a in shared_soup.find_all("a"):
        a_text = a.get_text(strip=True).lower()
        if any(k in a_text for k in ["download", "view more", "read more", "डाउनलोड", "थप पढ्नुहोस्"]):
            a.decompose()

    # Serialise the cleaned soup back to a string for trafilatura / readability
    # (they need an HTML string, not a soup object)
    cleaned_html = str(shared_soup)

    # Candidate 1: Trafilatura
    trafilatura_text: Optional[str] = None
    if use_trafilatura:
        try:
            import trafilatura
            logging.getLogger("trafilatura").setLevel(logging.ERROR)
            trafilatura_text = trafilatura.extract(
                cleaned_html, url=url, include_comments=False, include_tables=False
            )
        except Exception:
            pass

    # Candidate 2: readability-lxml
    readability_text: Optional[str] = None
    try:
        from readability import Document
        doc = Document(cleaned_html)
        r_html = doc.summary()
        r_soup = BeautifulSoup(r_html, "html.parser")
        candidate = r_soup.get_text("\n").strip()
        if len(candidate) > 100:
            readability_text = candidate
    except ImportError:
        pass
    except Exception:
        pass

    # Candidate 3: CSS selector targeting (reuse shared_soup)
    css_text: Optional[str] = None
    content_node = None
    for selector in CONTENT_SELECTORS:
        found = shared_soup.select_one(selector)
        if found and len(found.get_text()) > 200:
            content_node = found
            break

    if content_node:
        css_text = content_node.get_text("\n")
    else:
        # High text-density blocks with Devanagari content
        blocks = []
        for tag in shared_soup.find_all(["div", "section", "article"]):
            if not tag.find_parent(["div", "section", "article"]):
                text = tag.get_text(strip=True)
                if len(text) > 100 and devanagari_ratio(text) > 0.4:
                    blocks.append(tag.get_text("\n"))
        if blocks:
            css_text = "\n".join(blocks)

    # Candidate 4: Last-resort paragraph extraction
    paragraph_text: Optional[str] = None
    paragraphs = []
    for p in shared_soup.find_all("p"):
        p_text = p.get_text(strip=True)
        if len(p_text) > 30 and devanagari_ratio(p_text) > 0.3:
            paragraphs.append(p_text)
    if paragraphs:
        paragraph_text = "\n".join(paragraphs)

    # Voting: score all candidates, pick best
    candidates = [
        ("trafilatura", trafilatura_text),
        ("readability", readability_text),
        ("css_selectors", css_text),
        ("paragraphs", paragraph_text),
    ]

    best_score = -1.0
    extracted_text = ""

    for name, text in candidates:
        if not text:
            continue
        cleaned = clean_extracted_text(text)
        if not cleaned:
            continue
        dv_ratio = devanagari_ratio(cleaned)
        # Score = log(length) * (1 + Devanagari ratio)
        score = math.log(len(cleaned) + 1) * (1.0 + dv_ratio)
        if score > best_score:
            best_score = score
            extracted_text = text
            logger.debug("Extractor %s winning with score %.2f (dv: %.2f)", name, score, dv_ratio)

    cleaned_so_far = clean_extracted_text(extracted_text)

    # Strategy 5: Image OCR (scanned press releases)
    if ocr_enabled and url and (
        not cleaned_so_far
        or (len(cleaned_so_far.strip()) < 600 and devanagari_ratio(cleaned_so_far) < 0.2)
    ):
        logger.info(
            "Attempting image OCR for %s (len: %d, dv: %.2f)",
            url, len(cleaned_so_far.strip()), devanagari_ratio(cleaned_so_far),
        )
        ocr_text = _try_ocr_images(html, url)
        if ocr_text and len(ocr_text.strip()) > len(cleaned_so_far.strip()):
            logger.info("OCR successful: %d chars extracted", len(ocr_text))
            extracted_text = ocr_text
            cleaned_so_far = clean_extracted_text(extracted_text)

    # Strategy 6: Embedded PDF extraction
    if pdf_enabled and url and (
        not cleaned_so_far
        or (len(cleaned_so_far.strip()) < 600 and devanagari_ratio(cleaned_so_far) < 0.2)
    ):
        logger.info("Attempting embedded PDF extraction for %s", url)
        pdf_text = _try_embedded_pdfs(html, url)
        if pdf_text and len(pdf_text.strip()) > len(cleaned_so_far.strip()):
            logger.info("PDF extraction successful: %d chars extracted", len(pdf_text))
            extracted_text = pdf_text

    if not extracted_text:
        return ""

    return clean_extracted_text(extracted_text).strip()

def _try_ocr_images(html: str, base_url: str) -> str:
    """Fallback OCR: If the page just contains a scanned image, try to extract its text."""
    try:
        import pytesseract
        from PIL import Image
        import io
        from urllib.parse import urljoin
    except ImportError:
        return ""
        
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # Look for images inside typical content wrappers
        content_area = soup.select_one(", ".join(CONTENT_SELECTORS))
        images = content_area.find_all("img") if content_area else soup.find_all("img")
        
        best_text = ""
        
        for img in images:
            src = img.get("src")
            if not src:
                continue
                
            img_url = urljoin(base_url, src)
            
            try:
                resp = requests.get(img_url, verify=True, timeout=10,
                                    headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    continue
                    
                image_bytes = io.BytesIO(resp.content)
                img_obj = Image.open(image_bytes)
                
                # Only scan large images (likely to be documents, not icons)
                if img_obj.width > 400 and img_obj.height > 400:
                    from PIL import ImageOps
                    # Pre-process: Grayscale + Auto-contrast
                    img_processed = ImageOps.grayscale(img_obj)
                    img_processed = ImageOps.autocontrast(img_processed)
                    
                    text = pytesseract.image_to_string(img_processed, lang="nep+eng")
                    
                    if len(text.strip()) > len(best_text):
                        best_text = text.strip()
            except Exception as e:
                logger.debug(f"OCR failed for image {img_url}: {e}")
                continue
                
        return best_text
    except Exception as e:
        logger.debug(f"OCR overall failure: {e}")
        return ""

def _try_embedded_pdfs(html: str, base_url: str) -> str:
    """Fallback: Look for embedded PDF iframes or download links."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        pdf_urls = []
        
        # Check iframes
        for ifr in soup.find_all("iframe"):
            src = ifr.get("src", "")
            if ".pdf" in src.lower():
                pdf_urls.append(src)
                
        # Check links if no iframe found
        if not pdf_urls:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Only trust links that are obviously downloads or primary documents
                if ".pdf" in href.lower() and (
                    len(soup.find_all("a", href=True)) < 20 or 
                    "download" in a.get_text().lower() or 
                    "डाउनलोड" in a.get_text() or 
                    "विवरण" in a.get_text() or 
                    "सूचना" in a.get_text() or 
                    "file" in href.lower()
                ):
                    pdf_urls.append(href)
                    
        if pdf_urls:
            from urllib.parse import urljoin
            import requests
            
            # Try the first promising PDF link
            pdf_url = urljoin(base_url, pdf_urls[0])
            resp = requests.get(pdf_url, verify=True, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            
            if resp.status_code == 200 and b"%PDF" in resp.content[:10]:
                from nepali_corpus.core.services.scrapers.pdf.utils import _extract_text_from_pdf
                return _extract_text_from_pdf(resp.content)
                
    except Exception as e:
        logger.debug(f"Embedded PDF extraction failed: {e}")
        
    return ""

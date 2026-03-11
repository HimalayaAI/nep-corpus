from __future__ import annotations

import hashlib
import logging
import os
import time
import urllib3
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from nepali_corpus.core.services.scrapers.pdf.utils import HAS_PYMUPDF, _extract_text_from_pdf

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

def _cache_path(cache_dir: str, url: str, ext: str = ".html") -> str:
    h = hashlib.md5(url.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, f"{h}{ext}")


def fetch_content(url: str, cache_dir: str, timeout: int = 30, delay: float = 0.5) -> Tuple[Optional[bytes], str]:
    """Fetches url content and returns (bytes, content_type). Downloads PDFs and HTML."""
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
    try:
        r = requests.get(
            url, 
            timeout=timeout, 
            headers={"User-Agent": "NepaliCorpusBot/1.0 (+https://himalaya.ai)"}, 
            stream=True, 
            verify=False
        )
        r.raise_for_status()
        
        content_type = r.headers.get("Content-Type", "").lower()
        if "application/pdf" in content_type:
            c_type = "application/pdf"
            path = pdf_path
            # limit pdf to 50MB
            # we just stream and read up to 50MB
            data = r.raw.read(50 * 1024 * 1024)
        else:
            c_type = "text/html"
            path = html_path
            data = r.content
            
        with open(path, "wb") as f:
            f.write(data)
        return data, c_type
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return None, ""


def extract_text(data: bytes, content_type: str, url: Optional[str] = None, use_trafilatura: bool = True) -> str:
    if not data:
        return ""
        
    if "application/pdf" in content_type:
        if not HAS_PYMUPDF:
            logger.warning(f"Skipping PDF {url} because PyMuPDF is not installed")
            return ""
        try:
            return _extract_text_from_pdf(data).strip()
        except Exception as e:
            logger.warning(f"Failed to extract PDF {url}: {e}")
            return ""
            
    # Treat as HTML
    try:
        html = data.decode("utf-8")
    except UnicodeDecodeError:
        html = data.decode("utf-8", errors="ignore")

    if use_trafilatura:
        try:
            import trafilatura
            # silence trafilatura logs
            logging.getLogger("trafilatura").setLevel(logging.ERROR)

            text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
            if text:
                return text.strip()
        except Exception:
            pass

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    text = soup.get_text(" ")
    text = " ".join(text.split())
    return text.strip()

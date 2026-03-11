from __future__ import annotations

import logging
import time
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class ScraperBase:
    """Shared base for HTTP scrapers (session, fetch helpers)."""

    def __init__(self, base_url: str, delay: float = 0.5, verify_ssl: bool = False) -> None:
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.delay = delay
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "NepaliCorpusBot/1.0 (+https://himalaya.ai)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ne;q=0.8",
            }
        )
        self.session.verify = verify_ssl

    def fetch_page(self, url: str, timeout: int = 30) -> Optional[BeautifulSoup]:
        if not url:
            return None
        try:
            if self.delay:
                time.sleep(self.delay)
            response = self.session.get(url, timeout=timeout)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except Exception as exc:
            logger.warning("Failed to fetch %s: %s", url, exc)
            return None

    def base_domain(self) -> str:
        if not self.base_url:
            return ""
        return urlparse(self.base_url).netloc.lower().lstrip("www.")


__all__ = ["ScraperBase"]

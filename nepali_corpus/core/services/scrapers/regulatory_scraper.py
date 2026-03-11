from __future__ import annotations

import logging
import re
import time
from typing import Iterable, List, Optional, Set
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from .scraper_base import ScraperBase

from ...models import RawRecord
from ...models.government_schemas import RegistryEntry

logger = logging.getLogger(__name__)

KEYWORDS = (
    "notice",
    "press",
    "release",
    "news",
    "publication",
    "circular",
    "directive",
    "tender",
    "report",
    "policy",
    "announcement",
    "media",
    "event",
    "bulletin",
    "updates",
    "board",
    "download",
)

BLACKLIST_EXT = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
    ".rar",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".mp4",
    ".mp3",
)


def _normalize_domain(netloc: str) -> str:
    if netloc.startswith("www."):
        return netloc[4:]
    return netloc


def _same_domain(url: str, base_netloc: str) -> bool:
    try:
        netloc = _normalize_domain(urlparse(url).netloc)
    except Exception:
        return False
    base = _normalize_domain(base_netloc)
    return netloc == base or netloc.endswith("." + base)


def _strip_fragment(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def _is_candidate(url: str, text: str) -> bool:
    if not url:
        return False
    lower_url = url.lower()
    if any(lower_url.endswith(ext) for ext in BLACKLIST_EXT):
        return False
    if lower_url.startswith("mailto:") or lower_url.startswith("javascript:"):
        return False
    lower_text = (text or "").lower()
    if any(k in lower_url for k in KEYWORDS):
        return True
    if any(k in lower_text for k in KEYWORDS):
        return True
    if re.search(r"/(content|detail|news|notice|press|publication|events?)/", lower_url):
        return True
    return False


def _guess_category(url: str, text: str) -> str:
    lower = f"{url} {text}".lower()
    if "notice" in lower:
        return "notice"
    if "press" in lower or "release" in lower:
        return "press-release"
    if "news" in lower:
        return "news"
    if "circular" in lower:
        return "circular"
    if "tender" in lower:
        return "tender"
    return "regulatory"


def _listing_urls(entry: RegistryEntry, pages: int) -> List[str]:
    if not entry.base_url:
        return []
    base = entry.base_url.rstrip("/")
    if not entry.endpoints:
        return [base]
    urls: List[str] = []
    for endpoint in entry.endpoints.values():
        if not endpoint:
            continue
        endpoint = endpoint.strip()
        if "{page}" in endpoint:
            for page in range(1, max(pages, 1) + 1):
                urls.append(urljoin(base + "/", endpoint.format(page=page)))
        else:
            urls.append(urljoin(base + "/", endpoint))
    return urls or [base]


def _extract_links(html: str, base_url: str) -> List[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[tuple[str, str]] = []
    base_netloc = urlparse(base_url).netloc
    for a in soup.find_all("a", href=True):
        href = a.get("href") or ""
        if not href:
            continue
        url = urljoin(base_url, href)
        url = _strip_fragment(url)
        if not _same_domain(url, base_netloc):
            continue
        text = a.get_text(strip=True) or ""
        if not _is_candidate(url, text):
            continue
        links.append((url, text))
    return links


class RegulatoryScraper(ScraperBase):
    def __init__(self, entry: RegistryEntry, delay: float = 0.5):
        super().__init__(entry.base_url or "", delay=delay, verify_ssl=False)
        self.entry = entry

    def scrape(self, pages: int = 1, max_links: int = 120) -> List[RawRecord]:
        if not self.entry.base_url:
            return []
        listing_urls = _listing_urls(self.entry, pages)
        seen_links: Set[str] = set()
        records: List[RawRecord] = []
        for listing_url in listing_urls[: max(pages, 1)]:
            soup = self.fetch_page(listing_url)
            if soup is None:
                continue
            links = _extract_links(str(soup), listing_url)
            for url, text in links:
                if url in seen_links:
                    continue
                seen_links.add(url)
                if len(seen_links) > max_links:
                    break
                title = text or url.split("/")[-1].replace("-", " ").strip()
                records.append(
                    RawRecord(
                        source_id=self.entry.source_id or _normalize_domain(urlparse(self.entry.base_url).netloc),
                        source_name=self.entry.name or self.entry.source_id or self.entry.base_url,
                        url=url,
                        title=title if title else None,
                        category=_guess_category(url, title),
                        raw_meta={"listing_url": listing_url, "scraper_class": self.entry.scraper_class},
                    )
                )
            if len(seen_links) > max_links:
                break
        logger.info("Regulatory scrape %s: %s links", self.entry.source_id or self.entry.name, len(seen_links))
        return records


def fetch_raw_records(
    entries: Iterable[RegistryEntry],
    pages: int = 1,
    max_links: int = 120,
    delay: float = 0.5,
) -> List[RawRecord]:
    records: List[RawRecord] = []
    for entry in entries:
        scraper = RegulatoryScraper(entry, delay=delay)
        records.extend(scraper.scrape(pages=pages, max_links=max_links))
    return records


__all__ = ["fetch_raw_records", "RegulatoryScraper"]

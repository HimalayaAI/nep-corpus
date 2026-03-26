from __future__ import annotations

import json
import logging
import time
from typing import Iterable, List, Optional, Set
from urllib.parse import urlparse

import requests

from nepali_corpus.core.models import RawRecord
from nepali_corpus.core.services.scrapers.miner import DiscoveryMiner

logger = logging.getLogger(__name__)

DEFAULT_DOMAIN = "giwmscdntwo.gov.np"
DEFAULT_PREFIXES = [
    "/media/pdf_upload/",
    "/media/files/",
    "/media/app/public/",
]


def fetch_latest_cc_index() -> str:
    resp = requests.get("https://index.commoncrawl.org/collinfo.json", timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return "CC-MAIN-2024-46"
    return data[0]["id"]


def iter_cc_urls(domain: str, index_id: str) -> Iterable[str]:
    url = f"https://index.commoncrawl.org/{index_id}-index"
    params = {"url": f"{domain}/*", "output": "json"}
    resp = requests.get(url, params=params, stream=True, timeout=120)
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        u = rec.get("url")
        if u:
            yield u


def _filter_urls(
    urls: Iterable[str],
    domain: str,
    prefixes: List[str],
    require_pdf: bool = True,
) -> Iterable[str]:
    for url in urls:
        try:
            parsed = urlparse(url)
        except Exception:
            continue
        if domain and parsed.netloc.lower() != domain.lower():
            continue
        path = parsed.path or ""
        if prefixes and not any(path.startswith(p) for p in prefixes):
            continue
        if require_pdf and not path.lower().endswith(".pdf"):
            continue
        yield url


def _iter_miner_urls(
    seeds: List[str],
    domain: str,
    prefixes: List[str],
    max_pages: int,
    delay: float,
) -> Iterable[str]:
    for seed in seeds:
        miner = DiscoveryMiner(seed, delay=delay, verify_ssl=False)
        for batch in miner.discover_all(max_pages=max_pages):
            for url in _filter_urls(batch, domain=domain, prefixes=prefixes):
                yield url


def fetch_raw_records(
    domain: str = DEFAULT_DOMAIN,
    prefixes: Optional[List[str]] = None,
    discovery: str = "cc",
    cc_index: Optional[str] = None,
    limit: Optional[int] = None,
    miner_seeds: Optional[List[str]] = None,
    miner_max_pages: int = 200,
    miner_delay: float = 0.5,
) -> Iterable[RawRecord]:
    """Discover PDF URLs hosted on a gov CDN and emit RawRecord entries.

    discovery:
      - "cc": query Common Crawl index for domain paths
      - "miner": crawl provided seed sites via DiscoveryMiner
    """

    prefixes = prefixes or DEFAULT_PREFIXES
    discovery = (discovery or "cc").lower()
    seen: Set[str] = set()

    if discovery == "miner":
        seeds = miner_seeds or []
        if not seeds:
            raise ValueError("gov_cdn discovery=miner requires --gov-cdn-miner-seed")
        url_iter = _iter_miner_urls(
            seeds=seeds,
            domain=domain,
            prefixes=prefixes,
            max_pages=miner_max_pages,
            delay=miner_delay,
        )
    else:
        index_id = cc_index or fetch_latest_cc_index()
        url_iter = _filter_urls(
            iter_cc_urls(domain, index_id),
            domain=domain,
            prefixes=prefixes,
        )

    count = 0
    for url in url_iter:
        if url in seen:
            continue
        seen.add(url)

        parsed = urlparse(url)
        filename = parsed.path.rsplit("/", 1)[-1] if parsed.path else ""
        title = filename or None

        yield RawRecord(
            source_id=f"gov_cdn:{domain}",
            source_name=f"gov_cdn:{domain}",
            url=url,
            title=title,
            language="ne",
            category="gov_pdf",
            content_type="pdf",
            raw_meta={
                "gov_cdn": True,
                "discovery": discovery,
                "domain": domain,
            },
        )

        count += 1
        if limit and count >= limit:
            break
        if discovery == "cc" and count % 1000 == 0:
            time.sleep(0.1)


__all__ = ["fetch_raw_records"]

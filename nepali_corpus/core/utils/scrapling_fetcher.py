"""Scrapling integration for nep-corpus.

Provides a thin, optional wrapper around Scrapling's StealthyFetcher and
DynamicFetcher. All imports are guarded by try/except so the rest of the
pipeline works without Scrapling installed.

Usage
-----
from nepali_corpus.core.utils.scrapling_fetcher import HAS_SCRAPLING, stealth_fetch, dynamic_fetch

if HAS_SCRAPLING:
    data = stealth_fetch("https://example.com")

Design notes
------------
- ``stealth_fetch`` uses StealthyFetcher (real Chromium with a modified
  fingerprint). Handles most bot-detection walls including Cloudflare.
- ``dynamic_fetch`` uses DynamicFetcher (Playwright rendering) for JS-heavy
  pages whose content only appears after JS execution.
- Requires the ``[fetchers]`` extra: ``pip install scrapling[fetchers]`` plus
  ``scrapling install`` once to download the browser binaries.
- Both functions return ``Optional[bytes]`` — raw HTML bytes on success, ``None``
  on any failure or when Scrapling is unavailable.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from scrapling.fetchers import StealthyFetcher as _StealthyFetcher

    HAS_STEALTH = True
    logger.debug("Scrapling StealthyFetcher available")
except ImportError:
    HAS_STEALTH = False
    _StealthyFetcher = None  # type: ignore[assignment,misc]
    logger.debug("Scrapling stealth fetching unavailable (install scrapling[fetchers])")

try:
    from scrapling.fetchers import DynamicFetcher as _DynamicFetcher

    HAS_DYNAMIC = True
    logger.debug("Scrapling DynamicFetcher available")
except ImportError:
    HAS_DYNAMIC = False
    _DynamicFetcher = None  # type: ignore[assignment,misc]
    logger.debug("Scrapling dynamic (JS) fetching unavailable")

HAS_SCRAPLING = HAS_STEALTH or HAS_DYNAMIC

# Responses below this size are treated as likely bot-blocked / empty
MIN_CONTENT_BYTES: int = 2_000


def is_likely_blocked(html: Optional[str | bytes]) -> bool:
    """Heuristic: return True if the response looks like a bot-block page.

    Checks both raw size and common bot-wall fingerprints (Cloudflare, etc.).
    """
    if not html:
        return True
    content = html if isinstance(html, bytes) else html.encode("utf-8", errors="ignore")
    if len(content) < MIN_CONTENT_BYTES:
        return True
    block_markers = [
        b"cf-browser-verification",
        b"challenge-running",
        b"Enable JavaScript and cookies",
        b"Just a moment",
        b"Checking your browser",
        b"DDoS protection",
        b"Access denied",
        b"Ray ID",
    ]
    sample = content[:4096]
    return any(marker in sample for marker in block_markers)


def _response_bytes(page) -> Optional[bytes]:
    """Extract raw HTML bytes from a Scrapling Response object."""
    body = getattr(page, "body", None)
    if body is None:
        return None
    if isinstance(body, str):
        return body.encode("utf-8", errors="ignore")
    return body


def stealth_fetch(url: str, timeout: int = 30) -> Optional[bytes]:
    """Fetch *url* using Scrapling's StealthyFetcher (real Chromium fingerprint).

    Returns raw HTML bytes, or ``None`` on failure / Scrapling not installed.
    *timeout* is in seconds; Scrapling expects milliseconds internally.
    """
    if not HAS_STEALTH:
        return None
    try:
        page = _StealthyFetcher.fetch(
            url,
            headless=True,
            disable_resources=True,
            network_idle=True,
            timeout=timeout * 1000,
        )
        status = getattr(page, "status", None)
        if page and status == 200:
            content = _response_bytes(page)
            logger.debug("stealth_fetch OK: %d bytes from %s", len(content or b""), url)
            return content
        logger.warning("stealth_fetch got status %s for %s", status, url)
        return None
    except Exception as exc:
        logger.warning("stealth_fetch failed for %s: %s", url, exc)
        return None


def dynamic_fetch(url: str, timeout: int = 45, wait_selector: Optional[str] = None) -> Optional[bytes]:
    """Fetch *url* using Scrapling's DynamicFetcher (full Playwright JS rendering).

    Returns raw HTML bytes, or ``None`` on failure / Scrapling not installed.
    Falls back to :func:`stealth_fetch` when only the stealth engine is available.
    *timeout* is in seconds.
    """
    if not HAS_DYNAMIC:
        if HAS_STEALTH:
            logger.debug("dynamic_fetch called without dynamic engine — falling back to stealth_fetch")
            return stealth_fetch(url, timeout=timeout)
        return None
    try:
        kwargs: dict = {
            "headless": True,
            "disable_resources": True,
            "network_idle": True,
            "timeout": timeout * 1000,
        }
        if wait_selector:
            kwargs["wait_selector"] = wait_selector
        page = _DynamicFetcher.fetch(url, **kwargs)
        status = getattr(page, "status", None)
        if page and status == 200:
            content = _response_bytes(page)
            logger.debug("dynamic_fetch OK: %d bytes from %s", len(content or b""), url)
            return content
        logger.warning("dynamic_fetch got status %s for %s", status, url)
        return None
    except Exception as exc:
        logger.warning("dynamic_fetch failed for %s: %s", url, exc)
        return None


__all__ = [
    "HAS_SCRAPLING",
    "HAS_STEALTH",
    "HAS_DYNAMIC",
    "MIN_CONTENT_BYTES",
    "is_likely_blocked",
    "stealth_fetch",
    "dynamic_fetch",
]

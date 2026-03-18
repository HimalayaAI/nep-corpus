"""Per-domain rate limiting with circuit breakers for production scraping.

Provides:
- Token-bucket style per-domain rate limiting
- Global concurrency cap across all domains
- Auto-backoff on 429 responses
- Circuit breaker that skips domains after repeated failures
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger("nepali_corpus.rate_limiter")


class DomainRateLimiter:
    """Per-domain token-bucket rate limiter with circuit breakers.

    Usage::

        limiter = DomainRateLimiter(default_rate=2.0, max_concurrent=20)

        # Before each request:
        domain = urlparse(url).netloc
        if limiter.is_tripped(domain):
            continue  # skip this domain

        await limiter.acquire(domain)
        try:
            response = requests.get(url)
            if response.status_code == 429:
                limiter.record_throttle(domain, retry_after=response.headers.get("Retry-After"))
            limiter.record_success(domain)
        except Exception:
            if limiter.record_failure(domain):
                logger.warning("Circuit breaker tripped for %s", domain)
    """

    def __init__(
        self,
        default_rate: float = 2.0,
        max_concurrent: int = 20,
        circuit_breaker_threshold: int = 5,
    ) -> None:
        self._default_interval = 1.0 / max(default_rate, 0.1)
        self._max_concurrent = max_concurrent

        # Per-domain state
        self._domain_last_request: Dict[str, float] = {}
        self._domain_interval: Dict[str, float] = {}
        self._domain_locks: Dict[str, asyncio.Lock] = {}
        self._failure_counts: Dict[str, int] = defaultdict(int)
        self._tripped: Dict[str, bool] = {}
        self._circuit_breaker_threshold = circuit_breaker_threshold

        # Global concurrency
        self._global_semaphore = asyncio.Semaphore(max_concurrent)

    def _get_lock(self, domain: str) -> asyncio.Lock:
        if domain not in self._domain_locks:
            self._domain_locks[domain] = asyncio.Lock()
        return self._domain_locks[domain]

    def _get_interval(self, domain: str) -> float:
        return self._domain_interval.get(domain, self._default_interval)

    async def acquire(self, domain: str) -> None:
        """Wait until it's safe to make a request to this domain."""
        await self._global_semaphore.acquire()

        lock = self._get_lock(domain)
        async with lock:
            now = time.monotonic()
            last = self._domain_last_request.get(domain, 0.0)
            interval = self._get_interval(domain)
            wait = max(0.0, last + interval - now)
            if wait > 0:
                await asyncio.sleep(wait)
            self._domain_last_request[domain] = time.monotonic()

    def release(self) -> None:
        """Release the global concurrency slot after request completes."""
        self._global_semaphore.release()

    def record_success(self, domain: str) -> None:
        """Reset failure count on success."""
        self._failure_counts[domain] = 0

    def record_failure(self, domain: str) -> bool:
        """Record a failure. Returns True if circuit breaker tripped."""
        self._failure_counts[domain] += 1
        if self._failure_counts[domain] >= self._circuit_breaker_threshold:
            self._tripped[domain] = True
            logger.warning(
                "Circuit breaker tripped for %s after %d consecutive failures",
                domain,
                self._failure_counts[domain],
            )
            return True
        return False

    def record_throttle(self, domain: str, retry_after: Optional[str] = None) -> None:
        """Reduce rate for this domain after a 429 response."""
        current = self._get_interval(domain)
        # Double the interval (halve the rate)
        new_interval = min(current * 2, 30.0)  # Cap at 30s between requests
        self._domain_interval[domain] = new_interval
        logger.info(
            "Throttled %s: interval %.1fs -> %.1fs",
            domain,
            current,
            new_interval,
        )

    def is_tripped(self, domain: str) -> bool:
        """Return True if circuit breaker is open for this domain."""
        return self._tripped.get(domain, False)

    def set_crawl_delay(self, domain: str, delay: float) -> None:
        """Set the interval from robots.txt Crawl-delay directive."""
        if delay > 0:
            self._domain_interval[domain] = delay

    @staticmethod
    def extract_domain(url: str) -> str:
        """Extract domain from URL for rate limiting key."""
        return urlparse(url).netloc.lower()

    def stats(self) -> dict:
        """Return summary statistics."""
        return {
            "domains_tracked": len(self._domain_last_request),
            "domains_tripped": sum(1 for v in self._tripped.values() if v),
            "domains_throttled": sum(
                1 for d, i in self._domain_interval.items() if i > self._default_interval
            ),
        }


__all__ = ["DomainRateLimiter"]

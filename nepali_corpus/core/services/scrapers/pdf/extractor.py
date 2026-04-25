from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

import aiohttp
from pydantic import ConfigDict

from .utils import HAS_PYMUPDF, _extract_text_from_pdf

from nepali_corpus.core.models import RawRecord
from nepali_corpus.core.models.base import CorpusEntity
from nepali_corpus.core.utils.cleaning import clean_text
from nepali_corpus.core.utils.normalize import detect_nepali


class PdfJob(CorpusEntity):
    """A PDF download & extraction job.

    Inherits from:
        CorpusEntity – source_id, source_name
    """

    model_config = ConfigDict(frozen=True)

    url: str
    category: Optional[str] = None


def _pdf_path(output_dir: Path, url: str) -> Path:
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()
    return output_dir / f"{digest}.pdf"


# _extract_text_from_pdf moved to utils.py


async def _download_pdf(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Optional[bytes]:
    """Download PDF with exponential backoff retry on failures.
    
    Args:
        session: aiohttp client session
        url: PDF URL to download
        timeout: Timeout per attempt in seconds
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (exponentially increased)
    
    Returns:
        PDF bytes if successful, None otherwise
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status == 200:
                    return await resp.read()
                elif resp.status >= 500:  # Server error - retry
                    last_exception = Exception(f"HTTP {resp.status}")
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        await asyncio.sleep(delay)
                    continue
                else:  # Client error or 4xx - don't retry
                    return None
        except asyncio.TimeoutError as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            continue
        except Exception as e:
            last_exception = e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            continue
    
    # Failed after all retries
    return None


async def _handle_job(
    job: PdfJob,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    output_dir: Path,
    max_mb: int,
    min_chars: int,
    nepali_ratio: float,
    timeout: int,
    seen_url,
    mark_url,
) -> Optional[RawRecord]:
    async with semaphore:
        if await seen_url(job.url):
            return None
        await mark_url(job.url)

        pdf_bytes = await _download_pdf(session, job.url, timeout)
        if not pdf_bytes:
            return None

        if len(pdf_bytes) > max_mb * 1024 * 1024:
            return None

        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = _pdf_path(output_dir, job.url)
        try:
            pdf_path.write_bytes(pdf_bytes)
        except Exception:
            return None

        try:
            text = _extract_text_from_pdf(pdf_bytes)
        except Exception:
            return None

        text = clean_text(text)
        if len(text) < min_chars:
            return None
        if not detect_nepali(text, min_ratio=nepali_ratio):
            return None

        return RawRecord(
            source_id=job.source_id,
            source_name=job.source_name,
            url=job.url,
            title=None,
            content=text,
            content_type="pdf",
            language="ne",
            published_at=None,
            category=job.category,
            fetched_at=datetime.utcnow().isoformat(),
            raw_meta={
                "content_type": "pdf",
                "pdf_path": str(pdf_path),
            },
        )


async def extract_pdfs(
    jobs: Iterable[PdfJob],
    *,
    output_dir: str,
    max_workers: int = 10,
    max_mb: int = 100,
    min_chars: int = 200,
    nepali_ratio: float = 0.35,
    timeout: int = 60,
    seen_url=None,
    mark_url=None,
) -> List[RawRecord]:
    if not HAS_PYMUPDF:
        raise RuntimeError("PyMuPDF (fitz) is required for PDF extraction")

    if seen_url is None or mark_url is None:
        raise RuntimeError("seen_url and mark_url callbacks are required")

    output_path = Path(output_dir)
    semaphore = asyncio.Semaphore(max_workers)
    connector = aiohttp.TCPConnector(limit=max_workers, ssl=False)
    results: List[RawRecord] = []

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            _handle_job(
                job,
                session,
                semaphore,
                output_path,
                max_mb,
                min_chars,
                nepali_ratio,
                timeout,
                seen_url,
                mark_url,
            )
            for job in jobs
        ]
        for coro in asyncio.as_completed(tasks):
            record = await coro
            if record:
                results.append(record)

    return results


__all__ = ["PdfJob", "extract_pdfs", "HAS_PYMUPDF"]

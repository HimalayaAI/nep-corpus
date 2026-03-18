#!/usr/bin/env python3
"""
Ekantipur News Scraper

Scrapes news articles from Ekantipur (ekantipur.com), Nepal's largest Nepali-language
news site. Supports national + all 7 provincial pages. Uses async HTTP for speed.

Ekantipur's RSS feeds return 404, so this scraper parses HTML directly.

Usage:
    python ekantipur_scraper.py                        # National + all provinces
    python ekantipur_scraper.py --province gandaki      # Single province
    python ekantipur_scraper.py --national              # National only
    python ekantipur_scraper.py --output data/          # Save JSON per province
    python ekantipur_scraper.py --output corpus.jsonl --format jsonl

Requirements:
    pip install aiohttp beautifulsoup4 lxml
"""

import argparse
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp
from bs4 import BeautifulSoup

try:
    from ...models import RawRecord
    from ...models.news_schemas import EkantipurArticle
    from ...utils.content_types import identify_content_type
except ImportError:  # pragma: no cover
    from nepali_corpus.core.models import RawRecord
    from nepali_corpus.core.models.news_schemas import EkantipurArticle
    from nepali_corpus.core.utils.content_types import identify_content_type

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def article_to_raw(article: EkantipurArticle) -> RawRecord:
    scraped_at = article.scraped_at
    if hasattr(scraped_at, "isoformat"):
        scraped_at = scraped_at.isoformat()
    return RawRecord(
        source_id=article.source_id,
        source_name=article.source_name,
        url=article.url,
        title=article.title,
        summary=article.summary,
        language=article.language,
        published_at=article.published_at,
        province=article.province,
        content_type=identify_content_type(article.url),
        fetched_at=scraped_at,
        raw_meta={
            "image_url": article.image_url,
            "article_id": article.id,
        },
    )


# Province page mappings
PROVINCES = {
    "koshi":          {"name": "Koshi Province",          "path": "/pradesh-1", "number": 1},
    "madhesh":        {"name": "Madhesh Province",        "path": "/pradesh-2", "number": 2},
    "bagmati":        {"name": "Bagmati Province",        "path": "/pradesh-3", "number": 3},
    "gandaki":        {"name": "Gandaki Province",        "path": "/pradesh-4", "number": 4},
    "lumbini":        {"name": "Lumbini Province",        "path": "/pradesh-5", "number": 5},
    "karnali":        {"name": "Karnali Province",        "path": "/pradesh-6", "number": 6},
    "sudurpashchim":  {"name": "Sudurpashchim Province",  "path": "/pradesh-7", "number": 7},
}

BASE_URL = "https://ekantipur.com"

ARTICLE_PATTERN = re.compile(
    r"/(news|national|sports|entertainment|business|opinion|world|pradesh-\d+|"
    r"lifestyle|technology|health|education|feature|photo-feature|video)"
    r"/\d{4}/\d{2}/\d{2}/[^\"'#\s]+"
)


class EkantipurScraper:
    """Async scraper for Ekantipur news pages."""

    def __init__(self, max_concurrent: int = 5, timeout: int = 30, delay: float = 0.5):
        self.max_concurrent = max_concurrent
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.delay = delay
        self._session = None
        self._sem = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=self.max_concurrent * 2, limit_per_host=3),
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ne;q=0.8",
            },
        )
        self._sem = asyncio.Semaphore(self.max_concurrent)
        return self

    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()

    async def _fetch(self, url: str) -> Optional[str]:
        async with self._sem:
            await asyncio.sleep(self.delay)
            try:
                async with self._session.get(url) as r:
                    return await r.text() if r.status == 200 else None
            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                return None

    def _parse(self, html: str, source_id: str, source_name: str, province_name: str) -> List[EkantipurArticle]:
        soup = BeautifulSoup(html, "lxml")
        articles, seen = [], set()

        for link in soup.find_all("a", href=ARTICLE_PATTERN):
            url = link.get("href", "")
            if not url.startswith("http"):
                url = f"{BASE_URL}{url}"
            if url in seen:
                continue
            seen.add(url)

            # Extract title
            title = None
            for tag in ["h1", "h2", "h3", "h4", "h5"]:
                el = link.find(tag)
                if el:
                    title = el.get_text(strip=True)
                    break
            if not title:
                span = link.find("span", class_=re.compile(r"title|headline", re.I))
                if span:
                    title = span.get_text(strip=True)
            if not title:
                title = link.get_text(strip=True)
            if not title or len(title) < 10:
                title = link.get("title")
            if not title or len(title) < 10:
                continue
            title = re.sub(r"\s+", " ", title).strip()

            # Image
            img = link.find("img")
            image_url = (img.get("src") or img.get("data-src")) if img else None

            # Summary
            summary = None
            parent = link.parent
            if parent:
                for tag in ["p", "span", "div"]:
                    desc = parent.find(tag, class_=re.compile(r"desc|excerpt|summary|teaser", re.I))
                    if desc:
                        text = desc.get_text(strip=True)
                        if len(text) > 20:
                            summary = text[:500]
                            break

            # Date from URL
            published_at = None
            dm = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
            if dm:
                try:
                    published_at = datetime(int(dm.group(1)), int(dm.group(2)), int(dm.group(3))).isoformat()
                except ValueError:
                    pass

            # Language detection
            has_nepali = any("\u0900" <= c <= "\u097F" for c in title)

            articles.append(EkantipurArticle(
                id="", title=title, url=url, province=province_name,
                source_id=source_id, source_name=source_name,
                published_at=published_at, image_url=image_url,
                summary=summary, language="ne" if has_nepali else "en",
            ))

        logger.info(f"Parsed {len(articles)} articles from {source_name}")
        return articles

    async def scrape_province(self, province_key: str, max_articles: int = 50) -> List[EkantipurArticle]:
        if province_key not in PROVINCES:
            raise ValueError(f"Unknown province: {province_key}. Valid: {list(PROVINCES.keys())}")
        info = PROVINCES[province_key]
        url = f"{BASE_URL}{info['path']}"
        source_id = f"ekantipur_{province_key}"

        html = await self._fetch(url)
        if not html:
            return []

        articles = self._parse(html, source_id, f"Ekantipur {info['name']}", info["name"])
        # Deduplicate
        seen, unique = set(), []
        for a in articles:
            if a.url not in seen:
                seen.add(a.url)
                unique.append(a)
        return unique[:max_articles]

    async def scrape_national(self, max_articles: int = 50) -> List[EkantipurArticle]:
        html = await self._fetch(BASE_URL)
        if not html:
            return []
        articles = self._parse(html, "ekantipur_national", "Ekantipur National", "National")
        seen, unique = set(), []
        for a in articles:
            if a.url not in seen:
                seen.add(a.url)
                unique.append(a)
        return unique[:max_articles]

    async def scrape_all(self, max_per_province: int = 30) -> Dict[str, List[EkantipurArticle]]:
        results = {}
        # National
        results["national"] = await self.scrape_national(max_per_province)
        # All provinces
        for key in PROVINCES:
            try:
                results[key] = await self.scrape_province(key, max_per_province)
            except Exception as e:
                logger.error(f"Error scraping {key}: {e}")
                results[key] = []
        return results


async def run(args):
    async with EkantipurScraper() as scraper:
        all_articles = []

        if args.province:
            articles = await scraper.scrape_province(args.province, max_articles=50)
            all_articles.extend(articles)
        elif args.national:
            articles = await scraper.scrape_national(max_articles=50)
            all_articles.extend(articles)
        else:
            results = await scraper.scrape_all(max_per_province=30)
            for articles in results.values():
                all_articles.extend(articles)

        print(f"\nTotal articles: {len(all_articles)}")

        if not args.output:
            for a in all_articles[:10]:
                print(f"\n[{a.province}] {a.title[:70]}")
                print(f"  {a.url}")
            if len(all_articles) > 10:
                print(f"\n... and {len(all_articles) - 10} more")
            print("\nUse --output <path> to save.")
            return

        if args.format == "jsonl":
            with open(args.output, "w", encoding="utf-8") as f:
                for a in all_articles:
                    f.write(json.dumps(a.model_dump(mode="json"), ensure_ascii=False, default=str) + "\n")
            print(f"Saved to {args.output}")
        else:
            if args.output.endswith("/") or os.path.isdir(args.output):
                os.makedirs(args.output, exist_ok=True)
                by_prov = {}
                for a in all_articles:
                    by_prov.setdefault(a.source_id, []).append(a)
                for sid, arts in by_prov.items():
                    path = os.path.join(args.output, f"{sid}.json")
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump([a.model_dump(mode="json") for a in arts], f, ensure_ascii=False, indent=2, default=str)
                    print(f"  {sid}: {len(arts)} → {path}")
            else:
                with open(args.output, "w", encoding="utf-8") as f:
                    json.dump([a.model_dump(mode="json") for a in all_articles], f, ensure_ascii=False, indent=2, default=str)
                print(f"Saved to {args.output}")


def main():
    parser = argparse.ArgumentParser(description="Scrape Ekantipur news (national + 7 provinces)")
    parser.add_argument("--province", "-p", choices=list(PROVINCES.keys()), help="Scrape single province")
    parser.add_argument("--national", "-n", action="store_true", help="National news only")
    parser.add_argument("--output", "-o", help="Output path")
    parser.add_argument("--format", choices=["json", "jsonl"], default="json")
    parser.add_argument("--list", action="store_true", help="List provinces")
    args = parser.parse_args()

    if args.list:
        print("Ekantipur Provincial Pages:")
        for key, info in PROVINCES.items():
            print(f"  {key:15s} {info['name']} → {BASE_URL}{info['path']}")
        return

    asyncio.run(run(args))


def fetch_raw_records(province: Optional[str] = None, national: bool = False, max_pages: int = 5) -> List[RawRecord]:
    async def _fetch() -> List[RawRecord]:
        async with EkantipurScraper() as scraper:
            articles: List[EkantipurArticle] = []
            max_per = max_pages * 20  # Rough estimate
            if province:
                articles = await scraper.scrape_province(province, max_articles=max_per)
            elif national:
                articles = await scraper.scrape_national(max_articles=max_per)
            else:
                results = await scraper.scrape_all(max_per_province=max_per // 7)
                for group in results.values():
                    articles.extend(group)
            return [article_to_raw(a) for a in articles]

    return asyncio.run(_fetch())


if __name__ == "__main__":
    main()

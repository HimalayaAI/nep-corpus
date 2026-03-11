#!/usr/bin/env python3
"""
Generic Ministry Scraper for Nepal Government Websites

Scrapes press releases, notices, news, and circulars from 17+ Nepal government
ministry websites. Most Nepal govt sites follow a common CMS pattern with
/category/ URLs and /content/{id}/ article links.

Usage:
    python govt_scraper.py                          # List all ministries
    python govt_scraper.py --ministry mof            # Scrape Ministry of Finance
    python govt_scraper.py --ministry moest --pages 5  # Scrape 5 pages
    python govt_scraper.py --all --pages 2           # Scrape all ministries
    python govt_scraper.py --all --output data/      # Save JSON to data/

Requirements:
    pip install requests beautifulsoup4
"""

import argparse
import hashlib
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
import urllib3

try:
    from ...models import RawRecord
    from ...models.government_schemas import GovtPost, MinistryConfig, RegistryEntry
    from .scraper_base import ScraperBase
except ImportError:  # pragma: no cover
    from nepali_corpus.core.models import RawRecord
    from nepali_corpus.core.models.government_schemas import GovtPost, MinistryConfig, RegistryEntry
    from nepali_corpus.core.services.scrapers.scraper_base import ScraperBase

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def post_to_raw(post: GovtPost) -> RawRecord:
    scraped_at = post.scraped_at
    if hasattr(scraped_at, "isoformat"):
        scraped_at = scraped_at.isoformat()
    return RawRecord(
        source_id=post.source_id,
        source_name=post.source_name,
        url=post.url,
        title=post.title,
        language=post.language,
        published_at=post.date_ad.isoformat() if post.date_ad else None,
        date_bs=post.date_bs,
        category=post.category,
        fetched_at=scraped_at,
        raw_meta={
            "has_attachment": post.has_attachment,
            "attachment_urls": post.attachment_urls,
        },
    )


class MinistryScraper(ScraperBase):
    """
    Generic scraper for Nepal government ministry websites.

    Handles common patterns:
    - Category-based listings (/category/press-release/)
    - Content links (/content/{id}/)
    - Table-based layouts
    - Standard pagination (?page=N)
    """

    TABLE_SELECTORS = ["table.table", "table", ".table-responsive table"]
    NEPALI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

    def __init__(self, config: MinistryConfig, delay: float = 0.5):
        self.config = config
        super().__init__(config.base_url, delay=delay, verify_ssl=False)
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ne;q=0.8",
            }
        )

    def _fetch_page(self, url: str) -> Optional[BeautifulSoup]:
        soup = super().fetch_page(url)
        if soup is None:
            logger.error(f"Failed to fetch {url}")
        return soup

    def _convert_nepali_digits(self, text: str) -> str:
        return text.translate(self.NEPALI_DIGITS)

    def _extract_bs_date(self, text: str) -> Optional[str]:
        """Extract Bikram Sambat date from text (e.g. '2081-09-15')."""
        if not text:
            return None
        text = self._convert_nepali_digits(text)
        match = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", text)
        if match:
            year, month, day = match.groups()
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return None

    def _parse_category_posts(self, soup: BeautifulSoup, category: str, language: str) -> List[GovtPost]:
        """Parse posts from category listing page (most common Nepal govt format)."""
        posts = []
        content_links = soup.find_all("a", href=re.compile(r"/content/\d+/"))
        seen_urls = set()

        for link in content_links:
            url = link["href"]
            if not url.startswith("http"):
                url = f"{self.config.base_url}{url}"
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title = link.get_text(strip=True)
            if not title or len(title) < 5:
                parent = link.find_parent(["div", "li", "article", "h2", "h3", "h4"])
                if parent:
                    title_el = parent.find(["h2", "h3", "h4", "h5"])
                    title = title_el.get_text(strip=True) if title_el else parent.get_text(strip=True)[:150]

            title = re.sub(r"\d+\s*(month|day|week|year|hour|minute)s?\s*ago\s*$", "", title, flags=re.I).strip()
            title = re.sub(r"\s+", " ", title)
            if not title or len(title) < 5:
                continue
            if len(title) > 200:
                title = title[:197] + "..."

            date_bs = None
            parent = link.find_parent(["div", "li", "article", "tr"])
            if parent:
                date_bs = self._extract_bs_date(parent.get_text())

            has_attachment = False
            if parent:
                has_attachment = bool(parent.find("a", href=re.compile(r"\.(pdf|doc|docx|xls|xlsx)$", re.I)))

            post_id = hashlib.md5(f"{self.config.source_id}:{url}".encode()).hexdigest()[:12]
            posts.append(GovtPost(
                id=post_id, title=title, url=url,
                source_id=self.config.source_id,
                source_name=self.config.name,
                source_domain=self.config.base_url.replace("https://", "").replace("http://", ""),
                date_bs=date_bs, category=category, language=language,
                has_attachment=has_attachment,
            ))
        return posts

    def _parse_table_posts(self, soup: BeautifulSoup, category: str, language: str) -> List[GovtPost]:
        """Parse posts from table layout."""
        posts = []
        table = None
        for selector in self.TABLE_SELECTORS:
            table = soup.select_one(selector)
            if table:
                break
        if not table:
            return posts

        tbody = table.find("tbody") or table
        for row in tbody.find_all("tr"):
            link = row.find("a", href=True)
            if not link:
                continue
            title = re.sub(r"\d+\s*(month|day|week|year|hour|minute)s?\s*ago\s*$", "",
                           link.get_text(strip=True), flags=re.I).strip()
            if not title or len(title) < 5:
                continue
            url = link["href"]
            if not url.startswith("http"):
                url = f"{self.config.base_url}{url}"

            date_bs = None
            for cell in row.find_all("td"):
                date_bs = self._extract_bs_date(cell.get_text())
                if date_bs:
                    break

            has_attachment = bool(row.find("a", href=re.compile(r"\.(pdf|doc|docx|xls|xlsx)$", re.I)))
            post_id = hashlib.md5(f"{self.config.source_id}:{url}".encode()).hexdigest()[:12]
            posts.append(GovtPost(
                id=post_id, title=title, url=url,
                source_id=self.config.source_id,
                source_name=self.config.name,
                source_domain=self.config.base_url.replace("https://", "").replace("http://", ""),
                date_bs=date_bs, category=category, language=language,
                has_attachment=has_attachment,
            ))
        return posts

    def _get_next_page_url(self, soup: BeautifulSoup, current_url: str, page_num: int) -> Optional[str]:
        next_link = soup.find("a", {"rel": "next"})
        if next_link and next_link.get("href"):
            href = next_link["href"]
            return href if href.startswith("http") else f"{self.config.base_url}{href}"

        pagination = (soup.find("nav", {"aria-label": re.compile(r"pagination", re.I)}) or
                      soup.find("ul", class_=re.compile(r"pagination", re.I)) or
                      soup.find("div", class_=re.compile(r"pagination", re.I)))
        if pagination:
            next_page = pagination.find("a", string=str(page_num + 1))
            if not next_page:
                next_page = pagination.find("a", string=re.compile(f"^{page_num + 1}$"))
            if next_page and next_page.get("href"):
                href = next_page["href"]
                return href if href.startswith("http") else f"{self.config.base_url}{href}"

        base = current_url.split("?")[0]
        return f"{base}?page={page_num + 1}"

    def scrape_endpoint(self, endpoint_key: str, max_pages: int = 5) -> List[GovtPost]:
        """Scrape a specific endpoint (e.g. 'press_release', 'notice')."""
        if endpoint_key not in self.config.endpoints:
            raise ValueError(f"Unknown endpoint: {endpoint_key}. Available: {list(self.config.endpoints.keys())}")

        endpoint = self.config.endpoints[endpoint_key]
        language = "ne" if "_ne" in endpoint_key else "en"
        category = endpoint_key.replace("_en", "").replace("_ne", "").replace("-", "_")
        url = f"{self.config.base_url}{endpoint}"
        all_posts = []

        for page_num in range(1, max_pages + 1):
            logger.info(f"Scraping {self.config.source_id} {endpoint_key} page {page_num}")
            soup = self._fetch_page(url)
            if not soup:
                break

            posts = (self._parse_category_posts(soup, category, language) if
                     self.config.page_structure != "table" else
                     self._parse_table_posts(soup, category, language))

            if not posts:
                posts = self._parse_table_posts(soup, category, language)
            if not posts:
                break

            all_posts.extend(posts)
            next_url = self._get_next_page_url(soup, url, page_num)
            if next_url and next_url != url:
                url = next_url
            else:
                break

        # Deduplicate
        seen = set()
        unique = [p for p in all_posts if p.url not in seen and not seen.add(p.url)]
        logger.info(f"Total {self.config.source_id} {endpoint_key}: {len(unique)} posts")
        return unique

    def scrape_all(self, max_pages_per_endpoint: int = 3) -> Dict[str, List[GovtPost]]:
        """Scrape all configured endpoints for this ministry."""
        results = {}
        for endpoint_key in self.config.endpoints:
            try:
                results[endpoint_key] = self.scrape_endpoint(endpoint_key, max_pages=max_pages_per_endpoint)
            except Exception as e:
                logger.error(f"Error scraping {endpoint_key}: {e}")
                results[endpoint_key] = []
        return results


# ============ Pre-configured Nepal Ministries (17 total) ============

MINISTRIES = {
    "mof": MinistryConfig(
        source_id="mof", name="Ministry of Finance", name_ne="अर्थ मन्त्रालय",
        base_url="https://mof.gov.np",
        endpoints={"circular": "/category/circular"},
        priority=1,
    ),
    "moest": MinistryConfig(
        source_id="moest", name="Ministry of Education, Science and Technology",
        name_ne="शिक्षा, विज्ञान तथा प्रविधि मन्त्रालय",
        base_url="https://moest.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/", "news": "/category/news/"},
    ),
    "mohp": MinistryConfig(
        source_id="mohp", name="Ministry of Health and Population",
        name_ne="स्वास्थ्य तथा जनसङ्ख्या मन्त्रालय",
        base_url="https://mohp.gov.np",
        endpoints={"press_release": "/category/pressrelease"},
        priority=1,
    ),
    "mod": MinistryConfig(
        source_id="mod", name="Ministry of Defence", name_ne="रक्षा मन्त्रालय",
        base_url="https://mod.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/", "news": "/category/news/"},
        priority=1,
    ),
    "moald": MinistryConfig(
        source_id="moald", name="Ministry of Agriculture and Livestock Development",
        name_ne="कृषि तथा पशुपन्छी विकास मन्त्रालय",
        base_url="https://moald.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/"},
    ),
    "moics": MinistryConfig(
        source_id="moics", name="Ministry of Industry, Commerce and Supplies",
        name_ne="उद्योग, वाणिज्य तथा आपूर्ति मन्त्रालय",
        base_url="https://moics.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/", "news": "/category/news/", "circular": "/category/circular"},
    ),
    "moewri": MinistryConfig(
        source_id="moewri", name="Ministry of Energy, Water Resources and Irrigation",
        name_ne="ऊर्जा, जलस्रोत तथा सिँचाइ मन्त्रालय",
        base_url="https://moewri.gov.np",
        endpoints={"press_release": "/category/press-release/"},
    ),
    "mocit": MinistryConfig(
        source_id="mocit", name="Ministry of Communications and Information Technology",
        name_ne="सञ्चार तथा सूचना प्रविधि मन्त्रालय",
        base_url="https://mocit.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/", "news": "/category/news/"},
    ),
    "moless": MinistryConfig(
        source_id="moless", name="Ministry of Labour, Employment and Social Security",
        name_ne="श्रम, रोजगार तथा सामाजिक सुरक्षा मन्त्रालय",
        base_url="https://moless.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/", "news": "/category/news/"},
    ),
    "mowcsc": MinistryConfig(
        source_id="mowcsc", name="Ministry of Women, Children and Senior Citizens",
        name_ne="महिला, बालबालिका तथा ज्येष्ठ नागरिक मन्त्रालय",
        base_url="https://mowcsc.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/"},
    ),
    "mofe": MinistryConfig(
        source_id="mofe", name="Ministry of Forests and Environment",
        name_ne="वन तथा वातावरण मन्त्रालय",
        base_url="https://mofe.gov.np",
        endpoints={"press_release": "/category/press-release/", "news": "/category/news/", "circular": "/category/circular"},
    ),
    "mopit": MinistryConfig(
        source_id="mopit", name="Ministry of Physical Infrastructure and Transport",
        name_ne="भौतिक पूर्वाधार तथा यातायात मन्त्रालय",
        base_url="https://mopit.gov.np",
        endpoints={"press_release": "/category/press-release/", "news": "/category/news/"},
    ),
    "moljpa": MinistryConfig(
        source_id="moljpa", name="Ministry of Law, Justice and Parliamentary Affairs",
        name_ne="कानून, न्याय तथा संसदीय मामिला मन्त्रालय",
        base_url="https://moljpa.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/"},
    ),
    "mofaga": MinistryConfig(
        source_id="mofaga", name="Ministry of Federal Affairs and General Administration",
        name_ne="संघीय मामिला तथा सामान्य प्रशासन मन्त्रालय",
        base_url="https://mofaga.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/", "circular": "/category/circular"},
    ),
    "moys": MinistryConfig(
        source_id="moys", name="Ministry of Youth and Sports",
        name_ne="युवा तथा खेलकुद मन्त्रालय",
        base_url="https://moys.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/"},
    ),
    "mowss": MinistryConfig(
        source_id="mowss", name="Ministry of Water Supply",
        name_ne="खानेपानी मन्त्रालय",
        base_url="https://mowss.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/"},
    ),
    "mocta": MinistryConfig(
        source_id="mocta", name="Ministry of Culture, Tourism and Civil Aviation",
        name_ne="संस्कृति, पर्यटन तथा नागरिक उड्डयन मन्त्रालय",
        base_url="https://mocta.gov.np",
        endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/"},
    ),
}


def get_scraper(ministry_id: str) -> MinistryScraper:
    """Get a configured scraper for a ministry."""
    if ministry_id not in MINISTRIES:
        raise ValueError(f"Unknown ministry: {ministry_id}. Available: {list(MINISTRIES.keys())}")
    return MinistryScraper(MINISTRIES[ministry_id])


def fetch_raw_records(
    ministry_ids: Optional[List[str]] = None,
    pages: int = 3,
    registry_configs: Optional[Dict[str, MinistryConfig]] = None,
    allow_default: bool = True,
) -> List[RawRecord]:
    if registry_configs is None:
        if not allow_default:
            return []
        configs = MINISTRIES
    else:
        configs = registry_configs
    if not configs:
        return []
    targets = list(configs.keys()) if ministry_ids is None else list(ministry_ids)

    records: List[RawRecord] = []
    for ministry_id in targets:
        if ministry_id not in configs:
            raise ValueError(f"Unknown ministry: {ministry_id}")
        scraper = MinistryScraper(configs[ministry_id])
        results = scraper.scrape_all(max_pages_per_endpoint=pages)
        for posts in results.values():
            records.extend(post_to_raw(p) for p in posts)
    return records


def fetch_registry_records(
    entries: Optional[List[RegistryEntry]],
    pages: int = 3,
    allow_default: bool = True,
) -> List[RawRecord]:
    if not entries:
        if allow_default:
            return fetch_raw_records(pages=pages)
        return []

    ministry_configs: Dict[str, MinistryConfig] = {}
    regulatory_entries: List[RegistryEntry] = []
    other_entries: List[RegistryEntry] = []

    for entry in entries:
        if entry.scraper_class == "ministry_generic":
            if entry.source_id and entry.base_url:
                ministry_configs[entry.source_id] = MinistryConfig(
                    source_id=entry.source_id,
                    name=entry.name or entry.source_id,
                    name_ne=entry.name_ne or entry.source_id,
                    base_url=entry.base_url,
                    endpoints=entry.endpoints,
                    priority=entry.priority,
                )
        elif entry.scraper_class == "regulatory":
            regulatory_entries.append(entry)
        else:
            other_entries.append(entry)

    records: List[RawRecord] = []
    if ministry_configs:
        records.extend(
            fetch_raw_records(
                registry_configs=ministry_configs,
                pages=pages,
                allow_default=False,
            )
        )

    if regulatory_entries:
        from .regulatory_scraper import fetch_raw_records as fetch_regulatory
        records.extend(fetch_regulatory(regulatory_entries, pages=max(1, pages)))

    if other_entries:
        from .regulatory_scraper import fetch_raw_records as fetch_regulatory
        records.extend(fetch_regulatory(other_entries, pages=1))

    return records


__all__ = ["fetch_raw_records", "fetch_registry_records", "MinistryScraper"]


def main():
    parser = argparse.ArgumentParser(description="Scrape Nepal government ministry websites")
    parser.add_argument("--ministry", "-m", help="Ministry ID (e.g. mof, moest, mohp)")
    parser.add_argument("--all", action="store_true", help="Scrape all ministries")
    parser.add_argument("--pages", "-p", type=int, default=3, help="Max pages per endpoint (default: 3)")
    parser.add_argument("--output", "-o", help="Output directory for JSON files (default: print to stdout)")
    parser.add_argument("--list", "-l", action="store_true", help="List all configured ministries")
    args = parser.parse_args()

    if args.list or (not args.ministry and not args.all):
        print("=" * 70)
        print("Nepal Government Ministry Scraper")
        print(f"Configured ministries: {len(MINISTRIES)}")
        print("=" * 70)
        for mid, cfg in MINISTRIES.items():
            print(f"  {mid:10s}  {cfg.name}")
            print(f"  {'':10s}  {cfg.name_ne}")
            print(f"  {'':10s}  URL: {cfg.base_url}")
            print(f"  {'':10s}  Endpoints: {list(cfg.endpoints.keys())}")
            print()
        if not args.ministry and not args.all:
            print("Run with --ministry <id> or --all to start scraping.")
        return

    if args.output:
        os.makedirs(args.output, exist_ok=True)

    targets = list(MINISTRIES.keys()) if args.all else [args.ministry]

    for ministry_id in targets:
        print(f"\n{'=' * 50}")
        print(f"Scraping: {MINISTRIES[ministry_id].name}")
        print(f"{'=' * 50}")

        scraper = get_scraper(ministry_id)
        results = scraper.scrape_all(max_pages_per_endpoint=args.pages)

        total = sum(len(posts) for posts in results.values())
        print(f"\nTotal posts: {total}")

        for endpoint, posts in results.items():
            print(f"  {endpoint}: {len(posts)} posts")
            for p in posts[:3]:
                print(f"    - {p.title[:70]}")
                if p.date_bs:
                    print(f"      Date (BS): {p.date_bs}")

        if args.output:
            out_path = os.path.join(args.output, f"{ministry_id}.json")
            data = {ep: [p.model_dump(mode="json") for p in posts] for ep, posts in results.items()}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            print(f"\n  Saved to {out_path}")


if __name__ == "__main__":
    main()

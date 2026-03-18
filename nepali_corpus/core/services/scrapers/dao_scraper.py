#!/usr/bin/env python3
"""
DAO (District Administration Office) Scraper — All 77 Districts of Nepal

Scrapes press releases, notices, and circulars from Nepal's 77 District
Administration Offices. DAOs issue curfews, prohibitory orders, and local
emergency notifications.

URL Pattern: https://dao{district}.moha.gov.np

Usage:
    python dao_scraper.py                              # List all 77 districts
    python dao_scraper.py --district kathmandu          # Single district
    python dao_scraper.py --priority                    # 15 priority districts
    python dao_scraper.py --all --output data/dao/      # All 77 districts
    python dao_scraper.py --province Koshi              # All districts in a province

Requirements:
    pip install requests beautifulsoup4
"""

import argparse
import hashlib
import json
import logging
import os
import re
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
import urllib3

try:
    from ...models import RawRecord
    from ...models.government_schemas import DAOPost
    from .scraper_base import ScraperBase
except ImportError:  # pragma: no cover
    from nepali_corpus.core.models import RawRecord
    from nepali_corpus.core.models.government_schemas import DAOPost
    from nepali_corpus.core.services.scrapers.scraper_base import ScraperBase

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def post_to_raw(post: DAOPost) -> RawRecord:
    scraped_at = post.scraped_at
    if hasattr(scraped_at, "isoformat"):
        scraped_at = scraped_at.isoformat()
    return RawRecord(
        source_id=post.source,
        source_name=post.source_name,
        url=post.url,
        title=post.title,
        language="ne" if "-ne" in post.category else "en",
        date_bs=post.date_bs,
        category=post.category,
        province=post.province,
        district=post.district,
        fetched_at=scraped_at,
        raw_meta={
            "has_attachment": post.has_attachment,
        },
    )


class DAOScraper(ScraperBase):
    """Scraper for Nepal's 77 District Administration Office websites."""

    # All 77 districts organized by province
    DISTRICTS = {
        # Koshi Province (14 districts)
        "taplejung": {"province": "Koshi", "name": "Taplejung", "name_ne": "ताप्लेजुङ"},
        "panchthar": {"province": "Koshi", "name": "Panchthar", "name_ne": "पाँचथर"},
        "ilam": {"province": "Koshi", "name": "Ilam", "name_ne": "इलाम"},
        "jhapa": {"province": "Koshi", "name": "Jhapa", "name_ne": "झापा"},
        "morang": {"province": "Koshi", "name": "Morang", "name_ne": "मोरङ"},
        "sunsari": {"province": "Koshi", "name": "Sunsari", "name_ne": "सुनसरी"},
        "dhankuta": {"province": "Koshi", "name": "Dhankuta", "name_ne": "धनकुटा"},
        "terhathum": {"province": "Koshi", "name": "Terhathum", "name_ne": "तेह्रथुम"},
        "sankhuwasabha": {"province": "Koshi", "name": "Sankhuwasabha", "name_ne": "संखुवासभा"},
        "bhojpur": {"province": "Koshi", "name": "Bhojpur", "name_ne": "भोजपुर"},
        "solukhumbu": {"province": "Koshi", "name": "Solukhumbu", "name_ne": "सोलुखुम्बु"},
        "okhaldhunga": {"province": "Koshi", "name": "Okhaldhunga", "name_ne": "ओखलढुङ्गा"},
        "khotang": {"province": "Koshi", "name": "Khotang", "name_ne": "खोटाङ"},
        "udayapur": {"province": "Koshi", "name": "Udayapur", "name_ne": "उदयपुर"},

        # Madhesh Province (8 districts)
        "saptari": {"province": "Madhesh", "name": "Saptari", "name_ne": "सप्तरी"},
        "siraha": {"province": "Madhesh", "name": "Siraha", "name_ne": "सिराहा"},
        "dhanusa": {"province": "Madhesh", "name": "Dhanusa", "name_ne": "धनुषा"},
        "mahottari": {"province": "Madhesh", "name": "Mahottari", "name_ne": "महोत्तरी"},
        "sarlahi": {"province": "Madhesh", "name": "Sarlahi", "name_ne": "सर्लाही"},
        "rautahat": {"province": "Madhesh", "name": "Rautahat", "name_ne": "रौतहट"},
        "bara": {"province": "Madhesh", "name": "Bara", "name_ne": "बारा"},
        "parsa": {"province": "Madhesh", "name": "Parsa", "name_ne": "पर्सा"},

        # Bagmati Province (13 districts)
        "dolakha": {"province": "Bagmati", "name": "Dolakha", "name_ne": "दोलखा"},
        "sindhupalchok": {"province": "Bagmati", "name": "Sindhupalchok", "name_ne": "सिन्धुपाल्चोक"},
        "rasuwa": {"province": "Bagmati", "name": "Rasuwa", "name_ne": "रसुवा"},
        "dhading": {"province": "Bagmati", "name": "Dhading", "name_ne": "धादिङ"},
        "nuwakot": {"province": "Bagmati", "name": "Nuwakot", "name_ne": "नुवाकोट"},
        "kathmandu": {"province": "Bagmati", "name": "Kathmandu", "name_ne": "काठमाडौं"},
        "bhaktapur": {"province": "Bagmati", "name": "Bhaktapur", "name_ne": "भक्तपुर"},
        "lalitpur": {"province": "Bagmati", "name": "Lalitpur", "name_ne": "ललितपुर"},
        "kavrepalanchok": {"province": "Bagmati", "name": "Kavrepalanchok", "name_ne": "काभ्रेपलाञ्चोक"},
        "ramechhap": {"province": "Bagmati", "name": "Ramechhap", "name_ne": "रामेछाप"},
        "sindhuli": {"province": "Bagmati", "name": "Sindhuli", "name_ne": "सिन्धुली"},
        "makwanpur": {"province": "Bagmati", "name": "Makwanpur", "name_ne": "मकवानपुर"},
        "chitwan": {"province": "Bagmati", "name": "Chitwan", "name_ne": "चितवन"},

        # Gandaki Province (11 districts)
        "gorkha": {"province": "Gandaki", "name": "Gorkha", "name_ne": "गोरखा"},
        "lamjung": {"province": "Gandaki", "name": "Lamjung", "name_ne": "लमजुङ"},
        "tanahun": {"province": "Gandaki", "name": "Tanahun", "name_ne": "तनहुँ"},
        "syangja": {"province": "Gandaki", "name": "Syangja", "name_ne": "स्याङ्जा"},
        "kaski": {"province": "Gandaki", "name": "Kaski", "name_ne": "कास्की"},
        "manang": {"province": "Gandaki", "name": "Manang", "name_ne": "मनाङ"},
        "mustang": {"province": "Gandaki", "name": "Mustang", "name_ne": "मुस्ताङ"},
        "myagdi": {"province": "Gandaki", "name": "Myagdi", "name_ne": "म्याग्दी"},
        "parbat": {"province": "Gandaki", "name": "Parbat", "name_ne": "पर्वत"},
        "baglung": {"province": "Gandaki", "name": "Baglung", "name_ne": "बाग्लुङ"},
        "nawalpur": {"province": "Gandaki", "name": "Nawalpur", "name_ne": "नवलपुर"},

        # Lumbini Province (12 districts)
        "parasi": {"province": "Lumbini", "name": "Parasi", "name_ne": "परासी"},
        "rupandehi": {"province": "Lumbini", "name": "Rupandehi", "name_ne": "रुपन्देही"},
        "kapilvastu": {"province": "Lumbini", "name": "Kapilvastu", "name_ne": "कपिलवस्तु"},
        "palpa": {"province": "Lumbini", "name": "Palpa", "name_ne": "पाल्पा"},
        "arghakhanchi": {"province": "Lumbini", "name": "Arghakhanchi", "name_ne": "अर्घाखाँची"},
        "gulmi": {"province": "Lumbini", "name": "Gulmi", "name_ne": "गुल्मी"},
        "pyuthan": {"province": "Lumbini", "name": "Pyuthan", "name_ne": "प्यूठान"},
        "rolpa": {"province": "Lumbini", "name": "Rolpa", "name_ne": "रोल्पा"},
        "rukumeast": {"province": "Lumbini", "name": "Rukum East", "name_ne": "रुकुम पूर्व"},
        "dang": {"province": "Lumbini", "name": "Dang", "name_ne": "दाङ"},
        "banke": {"province": "Lumbini", "name": "Banke", "name_ne": "बाँके"},
        "bardiya": {"province": "Lumbini", "name": "Bardiya", "name_ne": "बर्दिया"},

        # Karnali Province (10 districts)
        "dolpa": {"province": "Karnali", "name": "Dolpa", "name_ne": "डोल्पा"},
        "mugu": {"province": "Karnali", "name": "Mugu", "name_ne": "मुगु"},
        "humla": {"province": "Karnali", "name": "Humla", "name_ne": "हुम्ला"},
        "jumla": {"province": "Karnali", "name": "Jumla", "name_ne": "जुम्ला"},
        "kalikot": {"province": "Karnali", "name": "Kalikot", "name_ne": "कालिकोट"},
        "dailekh": {"province": "Karnali", "name": "Dailekh", "name_ne": "दैलेख"},
        "jajarkot": {"province": "Karnali", "name": "Jajarkot", "name_ne": "जाजरकोट"},
        "rukumwest": {"province": "Karnali", "name": "Rukum West", "name_ne": "रुकुम पश्चिम"},
        "salyan": {"province": "Karnali", "name": "Salyan", "name_ne": "सल्यान"},
        "surkhet": {"province": "Karnali", "name": "Surkhet", "name_ne": "सुर्खेत"},

        # Sudurpashchim Province (9 districts)
        "bajura": {"province": "Sudurpashchim", "name": "Bajura", "name_ne": "बाजुरा"},
        "bajhang": {"province": "Sudurpashchim", "name": "Bajhang", "name_ne": "बझाङ"},
        "achham": {"province": "Sudurpashchim", "name": "Achham", "name_ne": "अछाम"},
        "doti": {"province": "Sudurpashchim", "name": "Doti", "name_ne": "डोटी"},
        "kailali": {"province": "Sudurpashchim", "name": "Kailali", "name_ne": "कैलाली"},
        "kanchanpur": {"province": "Sudurpashchim", "name": "Kanchanpur", "name_ne": "कञ्चनपुर"},
        "dadeldhura": {"province": "Sudurpashchim", "name": "Dadeldhura", "name_ne": "डडेलधुरा"},
        "baitadi": {"province": "Sudurpashchim", "name": "Baitadi", "name_ne": "बैतडी"},
        "darchula": {"province": "Sudurpashchim", "name": "Darchula", "name_ne": "दार्चुला"},
    }

    PRIORITY_DISTRICTS = [
        "kathmandu", "lalitpur", "bhaktapur", "kaski",
        "morang", "sunsari", "parsa", "chitwan", "rupandehi",
        "kailali", "banke", "dang", "jhapa", "sarlahi", "makwanpur",
    ]

    PAGES = {
        "press-release-en": "/en/page/press-release",
        "press-release-ne": "/page/press-release",
        "notice-en": "/en/page/notice",
        "notice-ne": "/page/notice",
        "circular-en": "/en/page/circular",
        "circular-ne": "/page/circular",
    }

    def __init__(self, delay: float = 0.5):
        super().__init__("", delay=delay, verify_ssl=False)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ne;q=0.8",
        })

    @staticmethod
    def get_dao_url(district: str) -> str:
        return f"https://dao{district.lower()}.moha.gov.np"

    def _fetch(self, url: str) -> Optional[BeautifulSoup]:
        soup = self.fetch_page(url)
        if soup is None:
            logger.error(f"Failed: {url}")
        return soup

    def _parse_posts(self, soup: BeautifulSoup, district_key: str, category: str) -> List[DAOPost]:
        posts = []
        info = self.DISTRICTS[district_key]
        base_url = self.get_dao_url(district_key)

        # Try table layout first (most common)
        table = soup.find("table")
        if table:
            tbody = table.find("tbody") or table
            for row in tbody.find_all("tr"):
                link = row.find("a", href=True)
                if not link:
                    continue
                title = re.sub(r"\d+\s*(month|day|week|year|hour|minute)s?\s*ago\s*$", "",
                               link.get_text(strip=True), flags=re.I).strip()
                if not title:
                    continue
                url = link["href"]
                if not url.startswith("http"):
                    url = f"{base_url}{url}"

                date_bs = None
                for cell in row.find_all("td"):
                    m = re.search(r"(20\d{2}-\d{2}-\d{2})", cell.get_text(strip=True))
                    if m:
                        date_bs = m.group(1)
                        break

                has_att = bool(row.find("a", href=re.compile(r"\.(pdf|doc|docx|xls|xlsx)$", re.I)))
                pid = hashlib.md5(url.encode()).hexdigest()[:12]
                posts.append(DAOPost(
                    id=pid, title=title, url=url, district=info["name"],
                    province=info["province"], date_bs=date_bs, category=category,
                    has_attachment=has_att, source_id=f"dao{district_key}.moha.gov.np",
                    source_name=f"DAO {info['name']}",
                ))
            return posts

        # Fallback: card/list layout
        cards = soup.find_all("div", class_=re.compile(r"card|news-item|post-item", re.I))
        if not cards:
            cards = soup.find_all("article")
        for card in cards:
            link = card.find("a", href=True)
            if not link:
                continue
            title_el = card.find(["h2", "h3", "h4", "h5"]) or link
            title = title_el.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            url = link["href"]
            if not url.startswith("http"):
                url = f"{base_url}{url}"

            date_bs = None
            date_el = card.find(class_=re.compile(r"date|time|posted", re.I))
            if date_el:
                m = re.search(r"(20\d{2}-\d{2}-\d{2})", date_el.get_text(strip=True))
                if m:
                    date_bs = m.group(1)

            pid = hashlib.md5(url.encode()).hexdigest()[:12]
            posts.append(DAOPost(
                id=pid, title=title, url=url, district=info["name"],
                province=info["province"], date_bs=date_bs, category=category,
                has_attachment=False, source_id=f"dao{district_key}.moha.gov.np",
                source_name=f"DAO {info['name']}",
            ))
        return posts

    def scrape_district(self, district_key: str, category: str = "notice-en", max_pages: int = 3) -> List[DAOPost]:
        """Scrape a single district's DAO."""
        if district_key not in self.DISTRICTS:
            raise ValueError(f"Unknown district: {district_key}")
        if category not in self.PAGES:
            raise ValueError(f"Unknown category: {category}. Available: {list(self.PAGES.keys())}")

        base_url = f"{self.get_dao_url(district_key)}{self.PAGES[category]}"
        all_posts, url = [], base_url

        for page in range(1, max_pages + 1):
            logger.info(f"Scraping DAO {district_key} {category} page {page}")
            soup = self._fetch(url)
            if not soup:
                break
            posts = self._parse_posts(soup, district_key, category)
            if not posts:
                break
            all_posts.extend(posts)
            # Next page
            next_link = soup.find("a", {"rel": "next"})
            if next_link and next_link.get("href"):
                href = next_link["href"]
                url = href if href.startswith("http") else f"{self.get_dao_url(district_key)}{href}"
            else:
                url = f"{base_url}?page={page + 1}"

        seen, unique = set(), []
        for p in all_posts:
            if p.url not in seen:
                seen.add(p.url)
                unique.append(p)
        logger.info(f"DAO {district_key}: {len(unique)} unique posts")
        return unique

    def scrape_by_province(self, province: str, categories=None, max_pages: int = 2) -> Dict[str, List[DAOPost]]:
        """Scrape all districts in a province."""
        if categories is None:
            categories = ["notice-en"]
        results = {}
        for dk, info in self.DISTRICTS.items():
            if info["province"] == province:
                district_posts = []
                for cat in categories:
                    try:
                        district_posts.extend(self.scrape_district(dk, cat, max_pages))
                    except Exception as e:
                        logger.error(f"DAO {dk} {cat}: {e}")
                results[dk] = district_posts
        return results


def fetch_raw_records(
    districts: Optional[List[str]] = None,
    province: Optional[str] = None,
    category: str = "notice-en",
    pages: int = 2,
) -> List[RawRecord]:
    scraper = DAOScraper()
    records: List[RawRecord] = []

    if province:
        results = scraper.scrape_by_province(province, [category], pages)
        for posts in results.values():
            records.extend(post_to_raw(p) for p in posts)
        return records

    if districts:
        for dk in districts:
            posts = scraper.scrape_district(dk, category, pages)
            records.extend(post_to_raw(p) for p in posts)
        return records

    for dk in DAOScraper.PRIORITY_DISTRICTS:
        posts = scraper.scrape_district(dk, category, pages)
        records.extend(post_to_raw(p) for p in posts)

    return records


def main():
    parser = argparse.ArgumentParser(description="Scrape Nepal DAO (District Administration Office) websites")
    parser.add_argument("--district", "-d", help="Single district (e.g. kathmandu, kaski)")
    parser.add_argument("--province", "-p", help="Province name (e.g. Koshi, Bagmati, Gandaki)")
    parser.add_argument("--priority", action="store_true", help="Scrape 15 priority districts only")
    parser.add_argument("--all", action="store_true", help="Scrape all 77 districts (slow!)")
    parser.add_argument("--category", "-c", default="notice-en",
                        choices=["notice-en", "notice-ne", "press-release-en", "press-release-ne", "circular-en", "circular-ne"])
    parser.add_argument("--pages", type=int, default=2, help="Max pages per endpoint")
    parser.add_argument("--output", "-o", help="Output directory for JSON")
    parser.add_argument("--list", action="store_true", help="List all districts")
    args = parser.parse_args()

    scraper = DAOScraper()

    if args.list or (not args.district and not args.priority and not args.all and not args.province):
        print("=" * 70)
        print(f"Nepal District Administration Offices — {len(DAOScraper.DISTRICTS)} districts")
        print("=" * 70)
        by_prov = {}
        for dk, info in DAOScraper.DISTRICTS.items():
            by_prov.setdefault(info["province"], []).append((dk, info))
        for prov, districts in by_prov.items():
            print(f"\n{prov} Province ({len(districts)} districts):")
            for dk, info in districts:
                priority = " *" if dk in DAOScraper.PRIORITY_DISTRICTS else ""
                print(f"  {dk:20s} {info['name']:20s} {info['name_ne']}{priority}")
        print(f"\n* = priority district ({len(DAOScraper.PRIORITY_DISTRICTS)} total)")
        print(f"\nURL pattern: https://dao<district>.moha.gov.np")
        return

    if args.output:
        os.makedirs(args.output, exist_ok=True)

    all_posts = {}

    if args.district:
        posts = scraper.scrape_district(args.district, args.category, args.pages)
        all_posts[args.district] = posts
    elif args.province:
        all_posts = scraper.scrape_by_province(args.province, [args.category], args.pages)
    elif args.priority:
        for dk in DAOScraper.PRIORITY_DISTRICTS:
            try:
                all_posts[dk] = scraper.scrape_district(dk, args.category, args.pages)
            except Exception as e:
                logger.error(f"{dk}: {e}")
                all_posts[dk] = []
    elif args.all:
        for dk in DAOScraper.DISTRICTS:
            try:
                all_posts[dk] = scraper.scrape_district(dk, args.category, max_pages=1)
            except Exception as e:
                logger.error(f"{dk}: {e}")
                all_posts[dk] = []

    total = sum(len(p) for p in all_posts.values())
    print(f"\nTotal: {total} posts from {len(all_posts)} districts")
    for dk, posts in all_posts.items():
        if posts:
            print(f"  {dk}: {len(posts)} posts")
            for p in posts[:2]:
                print(f"    - {p.title[:60]}")

    if args.output:
        for dk, posts in all_posts.items():
            path = os.path.join(args.output, f"dao_{dk}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump([p.model_dump(mode="json") for p in posts], f, ensure_ascii=False, indent=2, default=str)
        print(f"\nSaved to {args.output}/")


if __name__ == "__main__":
    main()

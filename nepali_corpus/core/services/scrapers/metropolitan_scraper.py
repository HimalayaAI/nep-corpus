"""
Metropolitan city scraper for Kathmandu, Pokhara, etc.
These sites often use different CMS patterns than federal ministries.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Iterable, List, Optional, Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .scraper_base import ScraperBase
from ...models import RawRecord
from ...models.government_schemas import RegistryEntry

logger = logging.getLogger(__name__)


class MetropolitanScraper(ScraperBase):
    """
    Specialized scraper for Nepal metropolitan city websites.
    
    Handles patterns like:
    - Kathmandu: /notice/2025/04/23, /news/...
    - Pokhara: /samachar/, /suchana/
    - Custom layouts with card-based designs
    """
    
    # Kathmandu.gov.np specific selectors
    KATHMANDU_SELECTORS = {
        'listing': [
            '.notice-list .notice-item',
            '.news-list .news-item', 
            '.post-list article',
            '.content-list .item',
            '.list-view article',
        ],
        'title': [
            'h2 a', 'h3 a', '.title a', '.post-title a',
            '.entry-title a', '.news-title a'
        ],
        'date': [
            '.date', '.post-date', '.published', '.entry-date',
            'time', '.meta-date'
        ],
        'content_wrapper': [
            '.entry-content', '.post-content', '.content-area',
            '.detail-content', '.news-detail', '.notice-detail',
            'article .content', '.main-content'
        ]
    }
    
    # Pokhara patterns (often in Nepali)
    POKHARA_PATTERNS = {
        'listing_paths': ['/samachar/', '/suchana/', '/news/', '/notice/'],
        'content_indicators': ['समाचार', 'सूचना', 'विवरण', 'पढ्नुहोस्']
    }
    
    def __init__(self, entry: RegistryEntry, delay: float = 0.5):
        super().__init__(entry.base_url or "", delay=delay, verify_ssl=False)
        self.entry = entry
        self.domain = urlparse(entry.base_url or "").netloc.lower()
        
        # Rotate user agents for stubborn sites
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]
    
    def _get_headers(self, index: int = 0) -> dict:
        return {
            "User-Agent": self.user_agents[index % len(self.user_agents)],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ne-NP,ne;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    
    def _discover_urls(self, max_links: int = 120) -> List[tuple[str, str, str]]:
        """Discover URLs with source-specific logic."""
        urls = []
        seen: Set[str] = set()
        
        if 'kathmandu.gov.np' in self.domain:
            urls.extend(self._scrape_kathmandu(max_links, seen))
        elif 'pokhara' in self.domain or 'pokharamun' in self.domain:
            urls.extend(self._scrape_pokhara(max_links, seen))
        else:
            urls.extend(self._scrape_generic(max_links, seen))
        
        return urls
    
    def _scrape_kathmandu(self, max_links: int, seen: Set[str]) -> List[tuple[str, str, str]]:
        """Kathmandu metropolitan specific scraping."""
        results = []
        
        # Common paths for Kathmandu
        paths = ['/notice/', '/news/', '/press-release/', '/announcement/']
        base_url = self.entry.base_url.rstrip('/')
        
        for path in paths:
            if len(results) >= max_links:
                break
                
            listing_url = f"{base_url}{path}"
            soup = self.fetch_page(listing_url)
            
            if not soup:
                continue
            
            # Try various listing selectors
            for selector in self.KATHMANDU_SELECTORS['listing']:
                items = soup.select(selector)
                for item in items:
                    if len(results) >= max_links:
                        break
                    
                    # Find title link
                    link = None
                    for title_sel in self.KATHMANDU_SELECTORS['title']:
                        link = item.select_one(title_sel)
                        if link:
                            break
                    
                    if not link:
                        link = item.find('a', href=True)
                    
                    if not link:
                        continue
                    
                    url = urljoin(base_url, link.get('href', ''))
                    if url in seen or not url.startswith('http'):
                        continue
                    
                    seen.add(url)
                    title = link.get_text(strip=True)
                    
                    # Extract date
                    date = None
                    for date_sel in self.KATHMANDU_SELECTORS['date']:
                        date_el = item.select_one(date_sel)
                        if date_el:
                            date = self._extract_bs_date(date_el.get_text())
                            if date:
                                break
                    
                    category = 'notice' if 'notice' in path else 'news'
                    results.append((url, title, category))
                    
                    if len(results) >= max_links:
                        break
        
        return results
    
    def _scrape_pokhara(self, max_links: int, seen: Set[str]) -> List[tuple[str, str, str]]:
        """Pokhara metropolitan specific scraping."""
        results = []
        base_url = self.entry.base_url.rstrip('/')
        
        # Pokhara specific paths (in Nepali and English)
        paths = self.entry.endpoints.values() if self.entry.endpoints else [
            '/samachar/', '/suchana/', '/news/', '/notice/',
            '/category/news/', '/category/notice/'
        ]
        
        for path in paths:
            if len(results) >= max_links:
                break
            
            listing_url = urljoin(base_url + '/', str(path))
            soup = self.fetch_page(listing_url)
            
            if not soup:
                continue
            
            # Look for article cards
            cards = soup.find_all(['article', 'div'], class_=re.compile(
                r'post|news|samachar|suchana|card|item|entry', re.I
            ))
            
            # Also try generic selectors
            if not cards:
                cards = soup.select('.post, .news-item, .entry, .content-item, .list-item')
            
            for card in cards:
                if len(results) >= max_links:
                    break
                
                link = card.find('a', href=True)
                if not link:
                    continue
                
                url = urljoin(base_url, link.get('href', ''))
                if url in seen or not url.startswith('http'):
                    continue
                
                # Skip pagination links
                if re.search(r'/page/\d+', url):
                    continue
                
                seen.add(url)
                title = link.get_text(strip=True) or card.get_text(strip=True)[:100]
                
                # Determine category from URL or path
                category = 'news'
                if any(x in url.lower() for x in ['suchana', 'notice', 'notice']):
                    category = 'notice'
                elif any(x in url.lower() for x in ['samachar', 'news']):
                    category = 'news'
                
                results.append((url, title, category))
        
        return results
    
    def _scrape_generic(self, max_links: int, seen: Set[str]) -> List[tuple[str, str, str]]:
        """Generic scraping for other metropolitan sites."""
        results = []
        base_url = self.entry.base_url.rstrip('/')
        
        # Common news/notice paths
        paths = list(self.entry.endpoints.values()) if self.entry.endpoints else ['/news/', '/notice/', '/press-release/']
        
        for path in paths:
            if len(results) >= max_links:
                break
            
            listing_url = urljoin(base_url + '/', str(path))
            soup = self.fetch_page(listing_url)
            
            if not soup:
                continue
            
            # Find all links that look like content
            for link in soup.find_all('a', href=True):
                if len(results) >= max_links:
                    break
                
                href = link.get('href', '')
                url = urljoin(base_url, href)
                
                # Filter for content URLs
                if url in seen:
                    continue
                if not url.startswith(base_url):
                    continue
                if any(url.lower().endswith(ext) for ext in ['.pdf', '.jpg', '.png', '.zip']):
                    continue
                
                # Must look like content URL
                if not re.search(r'/(news|notice|post|article|content|detail|samachar|suchana|press)', url.lower()):
                    continue
                
                title = link.get_text(strip=True)
                if not title or len(title) < 5:
                    continue
                
                seen.add(url)
                category = 'news' if 'news' in url.lower() else 'notice'
                results.append((url, title, category))
        
        return results
    
    def _extract_bs_date(self, text: str) -> Optional[str]:
        """Extract Bikram Sambat date."""
        if not text:
            return None
        
        # Nepali digits
        nepali_digits = str.maketrans("०१२३४५६७८९", "0123456789")
        text = text.translate(nepali_digits)
        
        # Match BS date patterns
        match = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", text)
        if match:
            year, month, day = match.groups()
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return None
    
    def scrape(self, pages: int = 1, max_links: int = 120) -> List[RawRecord]:
        """Scrape metropolitan site."""
        if not self.entry.base_url:
            return []
        
        discovered = self._discover_urls(max_links)
        records = []
        
        for url, title, category in discovered:
            post_id = hashlib.md5(f"{self.entry.source_id}:{url}".encode()).hexdigest()[:12]
            
            records.append(RawRecord(
                source_id=self.entry.source_id,
                source_name=self.entry.name or self.entry.source_id,
                url=url,
                title=title if title else None,
                category=category,
                language='ne',  # Metropolitan sites usually Nepali
                raw_meta={
                    'listing_url': self.entry.base_url,
                    'scraper_class': 'metropolitan',
                    'domain': self.domain
                }
            ))
        
        logger.info("Metropolitan scrape %s: %s records", 
                   self.entry.source_id, len(records))
        return records


def fetch_raw_records(
    entries: Iterable[RegistryEntry],
    pages: int = 1,
    max_links: int = 120,
    delay: float = 0.5,
) -> List[RawRecord]:
    """Fetch records from metropolitan entries."""
    records: List[RawRecord] = []
    for entry in entries:
        if entry.scraper_class == 'metropolitan':
            scraper = MetropolitanScraper(entry, delay=delay)
            records.extend(scraper.scrape(pages=pages, max_links=max_links))
    return records


__all__ = ["fetch_raw_records", "MetropolitanScraper"]

"""
Enhanced regulatory scraper with better content extraction.
Handles sites like NTA, CAAN, Immigration that use different CMS patterns.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .scraper_base import ScraperBase
from ...models import RawRecord
from ...models.government_schemas import RegistryEntry

logger = logging.getLogger(__name__)


class EnhancedRegulatoryScraper(ScraperBase):
    """
    Enhanced scraper for regulatory bodies with stubborn sites.
    
    Improvements over basic RegulatoryScraper:
    - Better URL pattern detection for content links
    - Handles NTA's /page/ structure
    - Handles CAAN's notice board
    - Handles sites with JavaScript-rendered content hints
    - Better title extraction
    """
    
    # Site-specific patterns
    SITE_PATTERNS = {
        'nta.gov.np': {
            'listing_paths': ['/page/6', '/page/23', '/page/24', '/page/25'],  # Notices, Press, News, Reports
            'content_selectors': [
                '.content-wrapper', '.entry-content', '.post-content',
                '.notice-content', '.news-detail', '.detail-content'
            ],
            'title_selectors': ['h1.entry-title', 'h2.entry-title', 'h1.post-title', '.page-title'],
            'link_patterns': [r'/notices?/', r'/news/', r'/press/', r'/reports?/'],
        },
        'caanepal.gov.np': {
            'listing_paths': ['/notices', '/news', '/circulars'],
            'content_selectors': [
                '.notice-detail', '.news-content', '.entry-content',
                '.content-area', '.main-content'
            ],
            'title_selectors': ['h1.title', 'h2.title', '.page-title', 'h1.entry-title'],
            'link_patterns': [r'/notices?/', r'/news/', r'/circulars?/'],
        },
        'immigration.gov.np': {
            'listing_paths': ['/news', '/notices', '/circulars'],
            'content_selectors': ['.entry-content', '.post-content', '.content'],
            'title_selectors': ['h1.entry-title', '.entry-title', 'h1'],
            'link_patterns': [r'/news/', r'/notices?/', r'/circulars?/'],
        },
        'customs.gov.np': {
            'listing_paths': ['/notices', '/news', '/circulars'],
            'content_selectors': ['.entry-content', '.post-content'],
            'title_selectors': ['h1.entry-title', '.entry-title'],
            'link_patterns': [r'/notices?/', r'/news/'],
        },
    }
    
    # Generic patterns for unknown sites
    GENERIC_PATTERNS = {
        'content_selectors': [
            'article', 'main', '.content', '.entry-content', '.post-content',
            '.detail-content', '.news-detail', '.notice-detail',
            '[itemprop="articleBody"]', '.main-content'
        ],
        'title_selectors': [
            'h1.entry-title', 'h1.post-title', 'h1.title', '.entry-title',
            '.post-title', 'h1', 'h2', '.page-title'
        ],
        'link_patterns': [
            r'/news/', r'/notices?/', r'/press-?releases?/', r'/circulars?/',
            r'/announcements?/', r'/publications?/', r'/tenders?/',
            r'/content/\d+', r'/post/\d+', r'/detail/\d+'
        ],
    }
    
    def __init__(self, entry: RegistryEntry, delay: float = 0.5):
        super().__init__(entry.base_url or "", delay=delay, verify_ssl=False)
        self.entry = entry
        self.domain = urlparse(entry.base_url or "").netloc.lower()
        
        # Get site-specific patterns or use generic
        self.patterns = self.SITE_PATTERNS.get(self.domain, self.GENERIC_PATTERNS)
        
        # Rotating user agents to avoid blocks
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        self.ua_index = 0
    
    def _get_headers(self) -> dict:
        headers = {
            "User-Agent": self.user_agents[self.ua_index % len(self.user_agents)],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ne-NP,ne;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
        self.ua_index += 1
        return headers
    
    def fetch_page(self, url: str, retries: int = 2) -> Optional[BeautifulSoup]:
        """Fetch page with retry and rotating headers."""
        for attempt in range(retries + 1):
            try:
                # Update headers for each attempt
                self.session.headers.update(self._get_headers())
                
                soup = super().fetch_page(url)
                if soup:
                    return soup
                    
                if attempt < retries:
                    time.sleep(self.delay * (attempt + 1))
                    
            except Exception as e:
                logger.debug(f"Fetch attempt {attempt + 1} failed for {url}: {e}")
                if attempt < retries:
                    time.sleep(self.delay * (attempt + 1) * 2)
        
        return None
    
    def _extract_links(self, soup: BeautifulSoup, listing_url: str) -> List[Tuple[str, str]]:
        """Extract content links from a listing page."""
        links = []
        seen: Set[str] = set()
        base_netloc = urlparse(listing_url).netloc
        
        # Find all links
        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            if not href:
                continue
            
            # Build full URL
            url = urljoin(listing_url, href)
            
            # Same domain check
            if urlparse(url).netloc != base_netloc:
                continue
            
            # Skip fragment-only URLs
            if url.split('#')[0] in seen:
                continue
            
            # Check against content patterns
            url_lower = url.lower()
            text = a.get_text(strip=True)
            text_lower = text.lower()
            
            is_content = False
            
            # Check URL patterns
            for pattern in self.patterns.get('link_patterns', self.GENERIC_PATTERNS['link_patterns']):
                if re.search(pattern, url_lower):
                    is_content = True
                    break
            
            # Check text keywords if URL doesn't match
            if not is_content:
                content_keywords = [
                    'notice', 'news', 'press', 'release', 'circular',
                    'सूचना', 'समाचार', 'विज्ञप्ति', 'प्रेस', 'प्रेस विज्ञप्ति',
                    'tender', 'publication', 'announcement', 'decision',
                ]
                if any(kw in text_lower for kw in content_keywords):
                    is_content = True
            
            # Skip file downloads
            if any(url_lower.endswith(ext) for ext in ['.pdf', '.doc', '.docx', '.xls', '.zip', '.jpg']):
                is_content = False
            
            # Skip pagination
            if re.search(r'/page/\d+|\?page=\d+|&page=\d+', url_lower):
                continue
            
            if is_content and len(text) > 3:
                seen.add(url.split('#')[0])
                links.append((url, text))
        
        return links
    
    def _discover_listing_urls(self, pages: int = 3) -> List[str]:
        """Get listing page URLs to scrape."""
        urls = []
        base_url = self.entry.base_url.rstrip('/')
        
        # Use site-specific paths or endpoints
        if self.domain in self.SITE_PATTERNS:
            paths = self.SITE_PATTERNS[self.domain].get('listing_paths', [])
            for path in paths:
                urls.append(urljoin(base_url + '/', path))
        
        # Add configured endpoints
        if self.entry.endpoints:
            for endpoint in self.entry.endpoints.values():
                if endpoint:
                    if '{page}' in endpoint:
                        for p in range(1, pages + 1):
                            urls.append(urljoin(base_url + '/', endpoint.format(page=p)))
                    else:
                        urls.append(urljoin(base_url + '/', endpoint))
        
        # Fallback to common paths
        if not urls:
            common_paths = ['/news/', '/notices/', '/press-release/', '/circulars/']
            for path in common_paths:
                urls.append(urljoin(base_url + '/', path))
        
        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        
        return unique[:pages * 3]  # Limit total listing pages
    
    def scrape(self, pages: int = 3, max_links: int = 120) -> List[RawRecord]:
        """Scrape regulatory site with enhanced discovery."""
        if not self.entry.base_url:
            return []
        
        listing_urls = self._discover_listing_urls(pages)
        seen_links: Set[str] = set()
        records: List[RawRecord] = []
        
        for listing_url in listing_urls:
            if len(records) >= max_links:
                break
            
            logger.debug(f"Scraping listing: {listing_url}")
            soup = self.fetch_page(listing_url)
            
            if not soup:
                logger.warning(f"Failed to fetch listing: {listing_url}")
                continue
            
            links = self._extract_links(soup, listing_url)
            
            for url, title in links:
                if len(records) >= max_links:
                    break
                
                if url in seen_links:
                    continue
                seen_links.add(url)
                
                # Clean title
                title = re.sub(r'\s+', ' ', title).strip()
                if len(title) > 200:
                    title = title[:197] + '...'
                
                # Guess category
                category = self._guess_category(url, title)
                
                post_id = hashlib.md5(f"{self.entry.source_id}:{url}".encode()).hexdigest()[:12]
                
                records.append(RawRecord(
                    source_id=self.entry.source_id,
                    source_name=self.entry.name or self.entry.source_id,
                    url=url,
                    title=title if title else None,
                    category=category,
                    language='ne',
                    raw_meta={
                        'listing_url': listing_url,
                        'scraper_class': 'enhanced_regulatory',
                        'domain': self.domain
                    }
                ))
        
        logger.info(f"EnhancedRegulatory scrape {self.entry.source_id}: {len(records)} records")
        return records
    
    def _guess_category(self, url: str, title: str) -> str:
        """Determine category from URL and title."""
        combined = f"{url} {title}".lower()
        
        if any(x in combined for x in ['notice', 'सूचना', 'suchana']):
            return 'notice'
        if any(x in combined for x in ['press', 'विज्ञप्ति', 'press-release']):
            return 'press-release'
        if any(x in combined for x in ['news', 'समाचार', 'samachar']):
            return 'news'
        if any(x in combined for x in ['circular', 'परिपत्र']):
            return 'circular'
        if any(x in combined for x in ['tender', 'टेन्डर', 'बोलपत्र']):
            return 'tender'
        if any(x in combined for x in ['report', 'प्रतिवेदन']):
            return 'report'
        
        return 'regulatory'


def fetch_raw_records(
    entries: Iterable[RegistryEntry],
    pages: int = 3,
    max_links: int = 120,
    delay: float = 0.5,
) -> List[RawRecord]:
    """Fetch records using enhanced regulatory scraper."""
    records: List[RawRecord] = []
    
    for entry in entries:
        # Use enhanced scraper for known problematic sites
        problematic_sites = [
            'nta.gov.np', 'caanepal.gov.np', 'immigration.gov.np',
            'customs.gov.np', 'ccmc.gov.np', 'dor.gov.np', 'apf.gov.np',
            'nso.gov.np', 'npc.gov.np'
        ]
        
        domain = urlparse(entry.base_url or "").netloc.lower()
        
        if entry.scraper_class in ['regulatory', 'enhanced_regulatory'] or \
           any(site in domain for site in problematic_sites):
            scraper = EnhancedRegulatoryScraper(entry, delay=delay)
            records.extend(scraper.scrape(pages=pages, max_links=max_links))
    
    return records


__all__ = ["fetch_raw_records", "EnhancedRegulatoryScraper"]

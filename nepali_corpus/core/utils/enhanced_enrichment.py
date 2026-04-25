"""
Enhanced content enrichment with Playwright support for JavaScript-rendered sites.
Addresses 0% enrichment for nta, kathmandu, pokhara and other stubborn sites.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import time
from typing import Optional, Tuple, Dict
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# Import original enrichment functions
from .enrichment import (
    CONTENT_SELECTORS, BOILERPLATE_TAGS, BOILERPLATE_SELECTORS,
    GLOBAL_NOISE_SELECTORS, _cache_path, _detect_encoding,
    extract_text as original_extract_text
)
from .normalize import devanagari_ratio
from .boilerplate import clean_extracted_text

logger = logging.getLogger(__name__)

# Global session with retry strategy for connection persistence
_session_cache: Dict[str, requests.Session] = {}

def get_session(domain: str) -> requests.Session:
    """Get or create a session for a domain with retry logic."""
    if domain not in _session_cache:
        session = requests.Session()
        
        # Add retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        _session_cache[domain] = session
    
    return _session_cache[domain]

# Sites known to need JavaScript rendering
JS_REQUIRED_SITES = [
    'nta.gov.np', 'www.nta.gov.np',
    'kathmandu.gov.np', 'www.kathmandu.gov.np',
    'pokharamun.gov.np', 'www.pokharamun.gov.np',
    'caanepal.gov.np', 'www.caanepal.gov.np',
    'immigration.gov.np', 'www.immigration.gov.np',
    'ccmc.gov.np', 'www.ccmc.gov.np',
    'dor.gov.np', 'www.dor.gov.np',
    'apf.gov.np', 'www.apf.gov.np',
    'customs.gov.np', 'www.customs.gov.np',
    'nso.gov.np', 'www.nso.gov.np',
    'npc.gov.np', 'www.npc.gov.np',
    'kvda.gov.np', 'www.kvda.gov.np',
    'ird.gov.np', 'www.ird.gov.np',
    'mofa.gov.np', 'www.mofa.gov.np',
    'moe.gov.np', 'www.moe.gov.np',
    'onlinekhabar.com', 'www.onlinekhabar.com',
    'ekantipur.com', 'www.ekantipur.com',
    'lalitpur.gov.np', 'www.lalitpur.gov.np',
    'bharatpur.gov.np', 'www.bharatpur.gov.np',
    'moha.gov.np', 'daokathmandu.moha.gov.np', 'daolalitpur.moha.gov.np',
    'daobhaktapur.moha.gov.np', 'daokaski.moha.gov.np', 'daomorang.moha.gov.np',
    'daosunsari.moha.gov.np', 'daodhankuta.moha.gov.np', 'daojhapa.moha.gov.np',
    'daoilam.moha.gov.np', 'daopanchthar.moha.gov.np', 'daotaplejung.moha.gov.np',
    'daosankhuwasabha.moha.gov.np', 'daobhojpur.moha.gov.np', 'daosolukhumbu.moha.gov.np',
    'daookhaldhunga.moha.gov.np', 'daokhotang.moha.gov.np', 'daoudayapur.moha.gov.np',
    'ratopati.com', 'www.ratopati.com',
]

# Sites that block regular requests - aggressive bot protection
BOT_PROTECTED_SITES = [
    'nta.gov.np', 'www.nta.gov.np',
    'kathmandu.gov.np', 'www.kathmandu.gov.np',
    'pokharamun.gov.np', 'www.pokharamun.gov.np',
    'caanepal.gov.np', 'www.caanepal.gov.np',
    'immigration.gov.np', 'www.immigration.gov.np',
    'onlinekhabar.com', 'www.onlinekhabar.com',
    'ekantipur.com', 'www.ekantipur.com',
    'lalitpur.gov.np', 'www.lalitpur.gov.np',
    'moha.gov.np', 'daokathmandu.moha.gov.np', 'daolalitpur.moha.gov.np',
    'ratopati.com', 'www.ratopati.com',
]


def _needs_js_rendering(url: str) -> bool:
    """Check if URL is from a site known to require JS."""
    domain = urlparse(url).netloc.lower()
    return any(site in domain for site in JS_REQUIRED_SITES)


def _is_bot_protected(url: str) -> bool:
    """Check if URL is from a bot-protected site."""
    domain = urlparse(url).netloc.lower()
    return any(site in domain for site in BOT_PROTECTED_SITES)


def _fetch_with_playwright(url: str, cache_dir: str, timeout: int = 30) -> Tuple[Optional[bytes], str]:
    """
    Fetch content using Playwright for JavaScript-rendered pages.
    Falls back to regular requests if Playwright not available.
    """
    html_path = _cache_path(cache_dir, url, ".html")
    
    # Check cache first
    if os.path.exists(html_path):
        with open(html_path, "rb") as f:
            return f.read(), "text/html"
    
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            # Use stealth mode
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="ne-NP",
                timezone_id="Asia/Kathmandu",
            )
            
            # Add stealth scripts to avoid detection
            context.add_init_script("""
                // Remove webdriver property
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                
                // Fake plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                
                // Fake languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['ne-NP', 'ne', 'en-US', 'en']
                });
                
                // Fake permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
                
                // Hide automation
                delete navigator.__proto__.webdriver;
                
                // Fake Chrome runtime
                window.chrome = {
                    runtime: {}
                };
                
                // Add fake canvas fingerprint randomization
                const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = function(type) {
                    if (type === 'image/png' && this.width > 50 && this.height > 50) {
                        // Add slight noise to canvas
                        const ctx = this.getContext('2d');
                        const imageData = ctx.getImageData(0, 0, this.width, this.height);
                        for (let i = 0; i < imageData.data.length; i += 4) {
                            if (Math.random() < 0.01) {
                                imageData.data[i] = Math.max(0, Math.min(255, imageData.data[i] + (Math.random() - 0.5) * 2));
                            }
                        }
                        ctx.putImageData(imageData, 0, 0);
                    }
                    return originalToDataURL.apply(this, arguments);
                };
            """)
            
            page = context.new_page()
            
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                # Wait a bit for any lazy-loaded content
                page.wait_for_timeout(2000)
                
                html = page.content()
                browser.close()
                
                data = html.encode('utf-8')
                
                # Cache the result
                with open(html_path, "wb") as f:
                    f.write(data)
                
                return data, "text/html"
                
            except Exception as e:
                browser.close()
                logger.warning(f"Playwright fetch failed for {url}: {e}")
                return None, ""
                
    except ImportError:
        logger.debug(f"Playwright not available for {url}, falling back to requests")
        return None, ""
    except Exception as e:
        logger.warning(f"Playwright error for {url}: {e}")
        return None, ""


def _fetch_with_retry(url: str, cache_dir: str, timeout: int = 30, delay: float = 1.0) -> Tuple[Optional[bytes], str]:
    """
    Fetch with multiple strategies:
    1. Check cache
    2. Regular requests with retry
    3. Playwright for JS sites
    4. Extended delay for bot-protected sites
    """
    # Check cache first
    html_path = _cache_path(cache_dir, url, ".html")
    pdf_path = _cache_path(cache_dir, url, ".pdf")
    
    if os.path.exists(html_path):
        with open(html_path, "rb") as f:
            return f.read(), "text/html"
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            return f.read(), "application/pdf"
    
    # Ensure cache directory exists
    os.makedirs(cache_dir, exist_ok=True)
    
    # Determine fetch strategy
    use_playwright = _needs_js_rendering(url)
    needs_delay = _is_bot_protected(url)
    
    if needs_delay:
        time.sleep(delay * 2)  # Extra delay for bot-protected sites
    
    # Try Playwright first for JS sites
    if use_playwright:
        data, ctype = _fetch_with_playwright(url, cache_dir, timeout)
        if data:
            return data, ctype
        # Fall through to regular fetch if Playwright fails
    
    # Regular requests with multiple user agents - expanded for bot evasion
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
    ]
    
    # Additional stealth headers
    accept_headers = [
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    ]
    
    for i, ua in enumerate(user_agents):
        try:
            headers = {
                "User-Agent": ua,
                "Accept": accept_headers[i % len(accept_headers)],
                "Accept-Language": "ne-NP,ne;q=0.9,en-US;q=0.8,en;q=0.7,hi;q=0.6",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cache-Control": "max-age=0",
                "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
            }
            
            # Use session for cookie persistence
            domain = urlparse(url).netloc
            session = get_session(domain)
            
            response = session.get(
                url, headers=headers, timeout=timeout,
                verify=False, allow_redirects=True
            )
            
            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "").lower()
                data = response.content
                
                # Cache the result
                if "pdf" in content_type:
                    with open(pdf_path, "wb") as f:
                        f.write(data)
                    return data, "application/pdf"
                else:
                    with open(html_path, "wb") as f:
                        f.write(data)
                    return data, "text/html"
            
            elif response.status_code in [403, 429, 503]:  # Forbidden, Rate limited, or Service Unavailable
                logger.warning(f"Got {response.status_code} for {url}, retry {i+1}/{len(user_agents)} with different UA")
                time.sleep(delay * (i + 1) + random.uniform(0.5, 1.5))  # Add random jitter
                continue
            
            else:
                logger.debug(f"HTTP {response.status_code} for {url}")
                time.sleep(delay + random.uniform(0.1, 0.5))
                
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout for {url} (attempt {i+1})")
            time.sleep(delay * (i + 1))
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error for {url}: {e}")
            time.sleep(delay * (i + 1))
        except Exception as e:
            logger.warning(f"Fetch error for {url}: {e}")
            time.sleep(delay)
    
    return None, ""


def extract_text_enhanced(
    data: bytes,
    content_type: str,
    url: Optional[str] = None,
    use_trafilatura: bool = True,
    ocr_enabled: bool = True,
    pdf_enabled: bool = True,
    cache_dir: str = ".cache"
) -> str:
    """
    Enhanced text extraction with better handling for stubborn sites.
    
    Key improvements:
    - Better encoding detection for Nepali sites
    - Expanded CSS selectors for government sites
    - Better fallback strategies
    - Site-specific extraction patterns
    """
    
    if not data:
        return ""
    
    # PDF handling
    if "pdf" in content_type.lower():
        if not pdf_enabled:
            return ""
        try:
            from ..services.scrapers.pdf.utils import _extract_text_from_pdf
            return _extract_text_from_pdf(data).strip()
        except Exception as e:
            logger.warning(f"PDF extraction failed for {url}: {e}")
            return ""
    
    # HTML extraction
    encoding = _detect_encoding(data)
    try:
        html = data.decode(encoding)
    except:
        try:
            html = data.decode("utf-8", errors="ignore")
        except:
            return ""
    
    extracted_text = ""
    domain = urlparse(url or "").netloc.lower()
    
    # Site-specific extraction for known problem sites
    if 'nta.gov.np' in domain:
        extracted_text = _extract_nta_content(html)
    elif 'kathmandu.gov.np' in domain:
        extracted_text = _extract_kathmandu_content(html)
    elif 'pokhara' in domain:
        extracted_text = _extract_pokhara_content(html)
    
    # If site-specific didn't work, try generic approaches
    if not extracted_text or len(extracted_text.strip()) < 100:
        # Try Rust extractor
        try:
            from rust_url_dedup import extract_text as rust_extract
            rust_text = rust_extract(html)
            if len(rust_text) > 400 and devanagari_ratio(rust_text) > 0.3:
                extracted_text = rust_text
        except:
            pass
    
    # Trafilatura fallback
    if not extracted_text or len(extracted_text.strip()) < 100:
        if use_trafilatura:
            try:
                import trafilatura
                logging.getLogger("trafilatura").setLevel(logging.ERROR)
                trafilatura_text = trafilatura.extract(
                    html, url=url, include_comments=False, include_tables=False
                )
                if trafilatura_text:
                    extracted_text = trafilatura_text
            except:
                pass
    
    # Readability fallback
    if not extracted_text or len(extracted_text.strip()) < 100:
        try:
            from readability import Document
            doc = Document(html)
            summary_html = doc.summary()
            summary_soup = BeautifulSoup(summary_html, "html.parser")
            candidate = summary_soup.get_text("\n").strip()
            if len(candidate) > 100:
                extracted_text = candidate
        except:
            pass
    
    # Expanded CSS selector targeting
    if not extracted_text or len(extracted_text.strip()) < 100:
        extracted_text = _extract_with_expanded_selectors(html)
    
    # Final cleanup
    if extracted_text:
        return clean_extracted_text(extracted_text).strip()
    
    return ""


def _extract_nta_content(html: str) -> str:
    """Site-specific extraction for NTA."""
    soup = BeautifulSoup(html, "html.parser")
    
    # NTA uses .entry-content or custom wrappers
    selectors = [
        '.entry-content', '.post-content', '.content-area',
        '.notice-content', '.news-content', '.detail-content',
        'article .content', '.main-content article'
    ]
    
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            text = el.get_text("\n").strip()
            if len(text) > 200:
                return text
    
    return ""


def _extract_kathmandu_content(html: str) -> str:
    """Site-specific extraction for Kathmandu Metropolitan."""
    soup = BeautifulSoup(html, "html.parser")
    
    # Kathmandu uses various content wrappers
    selectors = [
        '.notice-detail-content', '.news-detail-content',
        '.entry-content', '.post-content', '.content-wrapper',
        '.detail-content', '.main-content article',
        'article .entry-content'
    ]
    
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            text = el.get_text("\n").strip()
            if len(text) > 200:
                return text
    
    return ""


def _extract_pokhara_content(html: str) -> str:
    """Site-specific extraction for Pokhara Metropolitan."""
    soup = BeautifulSoup(html, "html.parser")
    
    # Pokhara uses Nepali CMS patterns
    selectors = [
        '.entry-content', '.post-content', '.content-area',
        '.news-detail', '.samachar-content', '.suchana-content',
        '.detail-content', 'article', '.main-content'
    ]
    
    for selector in selectors:
        el = soup.select_one(selector)
        if el:
            text = el.get_text("\n").strip()
            if len(text) > 200 and devanagari_ratio(text) > 0.3:
                return text
    
    return ""


def _extract_with_expanded_selectors(html: str) -> str:
    """Extract using expanded CSS selectors for government sites."""
    soup = BeautifulSoup(html, "html.parser")
    
    # Remove boilerplate
    for tag in soup(BOILERPLATE_TAGS):
        tag.extract()
    
    for selector in soup.select(", ".join(BOILERPLATE_SELECTORS)):
        selector.extract()
    
    # Remove noise
    for selector in GLOBAL_NOISE_SELECTORS:
        for el in soup.select(selector):
            el.decompose()
    
    # Try expanded selectors for government sites - comprehensive Nepali patterns
    gov_selectors = CONTENT_SELECTORS + [
        # Kathmandu/Pokhara/Lalitpur metropolitan
        '.notice-detail-content', '.news-detail-content',
        '.entry-content', '.post-content', '.content-wrapper',
        '.municipality-content', '.metro-content',
        # Generic government
        '.content-area', '.main-content article',
        '.detail-content', '.page-content', '.page-main',
        'article', '[itemprop="articleBody"]', '.article-content',
        # Nepali CMS patterns (samachar/suchana = news/notice)
        '.samachar-content', '.suchana-content',
        '.samachar-detail', '.suchana-detail',
        '.press-content', '.press-release-content',
        # Government department patterns
        '.publications-content', '.downloads-content',
        '.acts-content', '.rules-content',
        '.department-content', '.ministry-content',
        # Table content
        '.table-responsive', '.data-table', '.table-striped',
        # PDF listing pages
        '.pdf-content', '.document-content',
        # Common wrapper classes
        '.wrapper-content', '.container-content',
        '.main-wrapper', '.body-content',
        # Bootstrap patterns
        '.col-md-9', '.col-lg-9', '.col-sm-9',
        '.col-md-8', '.col-lg-8',
        # News sites
        '.news-article', '.news-item', '.news-details',
        '.story-content', '.story-detail',
        # Regulatory bodies
        '.regulation-content', '.circular-content',
        '.notification-content', '.order-content'
    ]
    
    best_text = ""
    for selector in gov_selectors:
        el = soup.select_one(selector)
        if el:
            text = el.get_text("\n").strip()
            # Prefer longer text with Devanagari
            score = len(text) * (1 + devanagari_ratio(text))
            best_score = len(best_text) * (1 + devanagari_ratio(best_text))
            if score > best_score and len(text) > 100:
                best_text = text
    
    return best_text


def enhanced_fetch_content(url: str, cache_dir: str, timeout: int = 30, delay: float = 1.0):
    """Enhanced fetch with multiple strategies."""
    return _fetch_with_retry(url, cache_dir, timeout, delay)


__all__ = [
    "extract_text_enhanced",
    "enhanced_fetch_content",
    "_needs_js_rendering",
]

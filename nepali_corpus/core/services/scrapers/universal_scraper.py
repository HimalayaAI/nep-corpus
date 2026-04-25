"""
Universal Scraper - Works with ANY Nepali website
Auto-detects PDFs, images, and HTML content
Extracts real content, filters boilerplate
"""

import re
import os
import time
import hashlib
from typing import Optional, List, Dict, Tuple
from urllib.parse import urljoin, urlparse
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    import requests
except ImportError:
    requests = None

try:
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    from PIL import Image
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


class UniversalScraper:
    """
    Universal scraper that handles:
    - HTML pages with embedded PDFs
    - HTML pages with scanned images (notices)
    - Plain HTML content
    - Direct PDF links
    """
    
    def __init__(self, cache_dir: str = ".universal_cache", timeout: int = 45):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.session = self._create_session()
        
    def _create_session(self):
        """Create session with proper headers"""
        if not requests:
            return None
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ne-NP,ne;q=0.9,en-US;q=0.8,en;q=0.7',
        })
        return session
    
    def scrape(self, url: str) -> Dict:
        """
        Main entry point - scrapes ANY URL
        Returns: {'text': str, 'title': str, 'source_type': str, 'success': bool}
        """
        print(f"[Universal] Scraping: {url[:60]}...")
        
        # Step 1: Check if direct PDF
        if url.lower().endswith('.pdf'):
            return self._scrape_pdf(url)
        
        # Step 2: Fetch HTML
        html, content_type = self._fetch(url)
        if not html:
            return {'text': '', 'title': '', 'source_type': 'none', 'success': False}
        
        # Step 3: Check content type from headers
        if content_type and 'pdf' in content_type.lower():
            return self._extract_pdf_bytes(html, url)
        
        # Step 4: Look for embedded PDFs
        pdf_links = self._find_pdf_links(html, url)
        if pdf_links:
            print(f"  Found {len(pdf_links)} PDF link(s), extracting...")
            for pdf_url in pdf_links[:3]:  # Try first 3 PDFs
                result = self._scrape_pdf(pdf_url)
                if result['success'] and self._is_real_content(result['text']):
                    result['source_type'] = 'pdf_embedded'
                    return result
        
        # Step 5: Look for notice images (scanned documents)
        image_links = self._find_notice_images(html, url)
        if image_links:
            print(f"  Found {len(image_links)} image(s), OCR extracting...")
            for img_url in image_links[:2]:  # Try first 2 images
                result = self._scrape_image(img_url)
                if result['success'] and self._is_real_content(result['text']):
                    result['source_type'] = 'image_ocr'
                    return result
        
        # Step 6: Extract HTML content
        result = self._scrape_html(html, url)
        if result['success'] and self._is_real_content(result['text']):
            result['source_type'] = 'html'
            return result
        
        # Step 7: Nothing worked
        return {'text': '', 'title': '', 'source_type': 'failed', 'success': False}
    
    def _fetch(self, url: str) -> Tuple[Optional[bytes], str]:
        """Fetch URL with retries"""
        if not self.session:
            return None, ''
        
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=self.timeout, verify=False)
                response.raise_for_status()
                return response.content, response.headers.get('content-type', '')
            except Exception as e:
                print(f"  Fetch attempt {attempt+1} failed: {e}")
                time.sleep(1 * (attempt + 1))
        return None, ''
    
    def _find_pdf_links(self, html: bytes, base_url: str) -> List[str]:
        """Find all PDF links in HTML"""
        if not BeautifulSoup:
            return []
        
        try:
            soup = BeautifulSoup(html, 'lxml')
        except:
            soup = BeautifulSoup(html, 'html.parser')
        
        pdf_links = []
        
        # Method 1: Direct .pdf links
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.lower().endswith('.pdf'):
                full_url = urljoin(base_url, href)
                pdf_links.append(full_url)
        
        # Method 2: Links with "download" text containing PDF
        for link in soup.find_all('a', href=True):
            text = link.get_text().lower()
            href = link['href'].lower()
            if 'download' in text or 'pdf' in href:
                if '.pdf' in href:
                    full_url = urljoin(base_url, link['href'])
                    if full_url not in pdf_links:
                        pdf_links.append(full_url)
        
        # Method 3: Links near notice/circular headings
        for heading in soup.find_all(['h1', 'h2', 'h3', 'h4']):
            text = heading.get_text().lower()
            if any(word in text for word in ['notice', 'circular', 'order', 'decision', 'press release']):
                # Look for PDF link in same section
                next_elem = heading.find_next()
                for _ in range(5):  # Check next 5 elements
                    if next_elem and next_elem.name == 'a':
                        href = next_elem.get('href', '')
                        if '.pdf' in href.lower():
                            full_url = urljoin(base_url, href)
                            if full_url not in pdf_links:
                                pdf_links.append(full_url)
                    if next_elem:
                        next_elem = next_elem.find_next()
        
        return list(set(pdf_links))  # Remove duplicates
    
    def _find_notice_images(self, html: bytes, base_url: str) -> List[str]:
        """Find large images that might be scanned notices"""
        if not BeautifulSoup:
            return []
        
        try:
            soup = BeautifulSoup(html, 'lxml')
        except:
            soup = BeautifulSoup(html, 'html.parser')
        
        images = []
        
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if not src:
                continue
            
            # Check size
            width = img.get('width', '')
            height = img.get('height', '')
            
            # Convert to int if possible
            try:
                w = int(width) if width and width.isdigit() else 0
                h = int(height) if height and height.isdigit() else 0
            except:
                w, h = 0, 0
            
            # Large images likely to be documents
            is_large = w > 500 or h > 700
            
            # Check if near notice text
            parent = img.find_parent(['div', 'article', 'section'])
            parent_text = parent.get_text().lower() if parent else ''
            is_notice = any(word in parent_text for word in ['notice', 'circular', 'order', 'decision'])
            
            if is_large or is_notice:
                full_url = urljoin(base_url, src)
                images.append(full_url)
        
        return list(set(images))
    
    def _scrape_pdf(self, url: str) -> Dict:
        """Scrape a PDF URL"""
        pdf_bytes, _ = self._fetch(url)
        if not pdf_bytes:
            return {'text': '', 'title': '', 'source_type': 'pdf', 'success': False}
        return self._extract_pdf_bytes(pdf_bytes, url)
    
    def _extract_pdf_bytes(self, pdf_bytes: bytes, url: str) -> Dict:
        """Extract text from PDF bytes"""
        if not PDF_AVAILABLE:
            return {'text': '', 'title': '', 'source_type': 'pdf', 'success': False}
        
        try:
            # Save to temp file
            temp_path = self.cache_dir / f"temp_{hashlib.md5(url.encode()).hexdigest()}.pdf"
            with open(temp_path, 'wb') as f:
                f.write(pdf_bytes)
            
            # Extract text
            text_parts = []
            with pdfplumber.open(temp_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)
            
            # Cleanup
            temp_path.unlink(missing_ok=True)
            
            full_text = '\n'.join(text_parts)
            
            return {
                'text': full_text,
                'title': self._extract_title_from_text(full_text) or Path(url).stem,
                'source_type': 'pdf',
                'success': len(full_text) > 100
            }
            
        except Exception as e:
            print(f"  PDF extraction failed: {e}")
            return {'text': '', 'title': '', 'source_type': 'pdf', 'success': False}
    
    def _scrape_image(self, url: str) -> Dict:
        """Scrape image with OCR"""
        if not OCR_AVAILABLE:
            return {'text': '', 'title': '', 'source_type': 'image', 'success': False}
        
        img_bytes, _ = self._fetch(url)
        if not img_bytes:
            return {'text': '', 'title': '', 'source_type': 'image', 'success': False}
        
        try:
            # Save temp image
            temp_path = self.cache_dir / f"temp_{hashlib.md5(url.encode()).hexdigest()}.png"
            with open(temp_path, 'wb') as f:
                f.write(img_bytes)
            
            # OCR
            image = Image.open(temp_path)
            text = pytesseract.image_to_string(image, lang='nep+eng')
            
            # Cleanup
            temp_path.unlink(missing_ok=True)
            
            return {
                'text': text,
                'title': '',
                'source_type': 'image_ocr',
                'success': len(text) > 50
            }
            
        except Exception as e:
            print(f"  OCR failed: {e}")
            return {'text': '', 'title': '', 'source_type': 'image', 'success': False}
    
    def _scrape_html(self, html: bytes, url: str) -> Dict:
        """Scrape HTML content"""
        if not BeautifulSoup:
            return {'text': '', 'title': '', 'source_type': 'html', 'success': False}
        
        try:
            soup = BeautifulSoup(html, 'lxml')
        except:
            soup = BeautifulSoup(html, 'html.parser')
        
        # Extract title
        title = ''
        if soup.title:
            title = soup.title.get_text().strip()
        
        # Try to find main content area
        content_selectors = [
            'article', 'main', '.content', '.entry-content', 
            '.post-content', '#content', '.article-body',
            '.notice-detail', '.news-detail', '.press-release'
        ]
        
        content = None
        for selector in content_selectors:
            content = soup.select_one(selector)
            if content:
                break
        
        # If no content area found, use body but remove nav/footer
        if not content:
            content = soup.body
            # Remove navigation, footer, sidebar
            for tag in content.find_all(['nav', 'footer', 'aside', 'header', 'script', 'style']):
                tag.decompose()
        
        text = content.get_text(separator='\n', strip=True) if content else ''
        
        return {
            'text': text,
            'title': title,
            'source_type': 'html',
            'success': len(text) > 200
        }
    
    def _is_real_content(self, text: str) -> bool:
        """Check if text is real content or boilerplate"""
        if not text:
            return False
        
        # Check for Nepali content first (Devanagari) - shorter is OK
        if self._has_devanagari(text) and len(text) > 50:
            return True
        
        # Check for government/official content patterns (shorter OK)
        text_lower = text.lower()
        gov_keywords = ['notice', 'circular', 'order', 'decision', 'announcement', 
                       'government', 'ministry', 'deadline', 'must register',
                       'public', 'official', 'late fee', 'transport']
        has_gov_keyword = any(kw in text_lower for kw in gov_keywords)
        # Match numeric dates (30/06/2024 or 06-30-2024) OR month name dates (June 30, 2024)
        has_date = bool(re.search(r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}[/-]\d{2,4}|(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{2,4})', text, re.IGNORECASE))
        
        if has_gov_keyword and has_date and len(text) > 50:
            return True
        
        # For other content, require longer text
        if len(text) < 200:
            return False
        
        text_lower = text.lower()
        
        # Reject obvious boilerplate
        boilerplate_indicators = [
            'lorem ipsum',
            'click here to download',
            'download pdf',
            'download file',
            'page not found',
            '404 error',
            'under construction',
        ]
        
        for indicator in boilerplate_indicators:
            if indicator in text_lower:
                return False
        
        # Too many links vs text = likely navigation
        link_count = text_lower.count('http') + text_lower.count('www.')
        if link_count > 5 and len(text) < 1000:
            return False
        
        # Check for Nepali content (Devanagari)
        if self._has_devanagari(text):
            return True
        
        # Check for government/official terms
        gov_terms = ['notice', 'circular', 'order', 'decision', 'press release', 
                     'public', 'announcement', 'government', 'ministry']
        if any(term in text_lower for term in gov_terms):
            return True
        
        # Has dates = likely real content
        if re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', text):
            return True
        
        return len(text) > 500  # Long text is probably real
    
    def _has_devanagari(self, text: str) -> bool:
        """Check if text contains Nepali/Devanagari characters"""
        for char in text:
            if '\u0900' <= char <= '\u097F':  # Devanagari range
                return True
        return False
    
    def _extract_title_from_text(self, text: str) -> str:
        """Try to extract title from first few lines of text"""
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if lines:
            # First non-empty line that's not too long
            for line in lines[:5]:
                if 10 < len(line) < 200:
                    return line
        return ''


# Convenience function
def scrape_universal(url: str, cache_dir: str = ".universal_cache") -> Dict:
    """One-line universal scraper"""
    scraper = UniversalScraper(cache_dir=cache_dir)
    return scraper.scrape(url)

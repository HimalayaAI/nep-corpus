import logging
import re
import hashlib
import random
from typing import List, Optional, Dict, Any, Generator
from urllib.parse import quote_plus, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

try:
    from ...models import RawRecord
    from .scraper_base import ScraperBase
except ImportError:
    from nepali_corpus.core.models import RawRecord
    from nepali_corpus.core.services.scrapers.scraper_base import ScraperBase

logger = logging.getLogger(__name__)

class NitterScraper(ScraperBase):
    """
    Scraper for Nitter instances with instance rotation and concurrency.
    """
    def __init__(self, base_urls: List[str] = None, delay: float = 0.5):
        # Initialize with the first URL, but we will rotate
        self.base_urls = base_urls or ["https://nitter.poast.org"]
        super().__init__(self.base_urls[0], delay=delay, verify_ssl=False)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        })

    def _get_random_instance(self) -> str:
        return random.choice(self.base_urls)

    def rotating_fetch(self, path: str) -> Optional[BeautifulSoup]:
        """Fetch a page trying different instances if one fails (e.g. 403)."""
        tried = set()
        urls_to_try = list(self.base_urls)
        random.shuffle(urls_to_try)

        for base in urls_to_try:
            full_url = urljoin(base, path)
            try:
                response = self.session.get(full_url, timeout=10)
                if response.status_code == 200:
                    return BeautifulSoup(response.text, "html.parser")
                elif response.status_code == 403:
                    logger.warning(f"Instance {base} returned 403 for {path}. Trying another...")
                    continue
                else:
                    logger.debug(f"Instance {base} returned {response.status_code} for {path}")
            except Exception as e:
                logger.debug(f"Error connecting to {base}: {e}")
                continue
        
        logger.error(f"All Nitter instances failed for {path}")
        return None

    def fetch_user_tweets(self, username: str, max_pages: int = 1) -> List[RawRecord]:
        username = username.lstrip('@')
        records = []
        path = f"/{username}"
        
        for page in range(1, max_pages + 1):
            soup = self.rotating_fetch(path)
            if not soup:
                break
                
            tweets = soup.select(".timeline-item")
            if not tweets:
                break
                
            for tweet in tweets:
                if "unavailable" in tweet.get("class", []):
                    continue
                    
                content_el = tweet.select_one(".tweet-content")
                if not content_el:
                    continue
                    
                text = content_el.get_text(separator=" ", strip=True)
                link_el = tweet.select_one(".tweet-link")
                # Use a generic nitter URL or just leave it relative if we don't know the final instance
                tweet_url = urljoin(self.base_url, link_el["href"]) if link_el else f"{self.base_url}/{username}"
                
                date_el = tweet.select_one(".tweet-date a")
                date_str = date_el.get("title") if date_el else None
                
                records.append(RawRecord(
                    source_id=f"social:{username}",
                    source_name=f"@{username}",
                    url=tweet_url,
                    title=text[:100] + ("..." if len(text) > 100 else ""),
                    content=text,
                    language="ne" if any('\u0900' <= c <= '\u097f' for c in text) else "en",
                    published_at=date_str,
                    category="Social",
                    raw_meta={"username": username, "type": "user_tweet"}
                ))
            
            # Pagination
            next_link = soup.select_one(".show-more a")
            if next_link and next_link.get("href"):
                path = next_link["href"] # Nitter next links are usually absolute paths
            else:
                break
                
        return records

    def fetch_search_tweets(self, query: str, max_pages: int = 1) -> List[RawRecord]:
        records = []
        encoded_query = quote_plus(query)
        path = f"/search?q={encoded_query}"
        
        for page in range(1, max_pages + 1):
            soup = self.rotating_fetch(path)
            if not soup:
                break
                
            tweets = soup.select(".timeline-item")
            if not tweets:
                break
                
            for tweet in tweets:
                content_el = tweet.select_one(".tweet-content")
                if not content_el:
                    continue
                    
                text = content_el.get_text(separator=" ", strip=True)
                user_el = tweet.select_one(".username")
                username = user_el.get_text(strip=True).lstrip('@') if user_el else "unknown"
                
                link_el = tweet.select_one(".tweet-link")
                tweet_url = urljoin(self.base_url, link_el["href"]) if link_el else f"{self.base_url}/search"
                
                records.append(RawRecord(
                    source_id=f"search:{hashlib.md5(query.encode()).hexdigest()[:8]}",
                    source_name=f"Search: {query}",
                    url=tweet_url,
                    title=text[:100] + ("..." if len(text) > 100 else ""),
                    content=text,
                    language="ne" if any('\u0900' <= c <= '\u097f' for c in text) else "en",
                    category="Social",
                    raw_meta={"query": query, "username": username, "type": "search_tweet"}
                ))
            
            next_link = soup.select_one(".show-more a")
            if next_link and next_link.get("href"):
                path = next_link["href"]
            else:
                break
                
        return records

def fetch_raw_records(
    config_path: Optional[str] = None,
    max_pages: int = 1,
) -> Generator[RawRecord, None, None]:
    """
    Load social sources from YAML and fetch tweets concurrently.
    Yields records as they are fetched.
    """
    import yaml
    from pathlib import Path
    
    if config_path is None:
        # Default path
        config_path = str(Path(__file__).parents[4] / "sources" / "social_sources.yaml")
        
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to load social config from {config_path}: {e}")
        return
        
    # Get Nitter instances
    instances = config.get("nitter_instances", [])
    base_urls = [i.get("url") for i in instances if i.get("url")]
    if not base_urls:
        base_urls = ["https://nitter.poast.org", "https://nitter.privacydev.net"]
        
    scraper = NitterScraper(base_urls)
    tasks = []
    
    # Define tasks for parallel execution
    # 1. Accounts
    accounts = config.get("accounts", [])
    for acc in accounts:
        username = acc.get("username")
        if username:
            tasks.append((scraper.fetch_user_tweets, username))
            
    # 2. Hashtags
    hashtags = config.get("hashtags", [])
    for tag in hashtags:
        label = tag.get("tag")
        if label:
            tasks.append((scraper.fetch_search_tweets, f"#{label}"))
            
    # 3. Searches
    searches = config.get("searches", [])
    for item in searches:
        query = item.get("query")
        if query:
            tasks.append((scraper.fetch_search_tweets, query))
            
    # Execute tasks concurrently
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_task = {executor.submit(func, arg, max_pages): (func, arg) for func, arg in tasks}
        
        for future in as_completed(future_to_task):
            task_info = future_to_task[future]
            try:
                records = future.result()
                if records:
                    logger.info(f"Successfully fetched {len(records)} records for {task_info[1]}")
                    for rec in records:
                        yield rec
            except Exception as e:
                logger.error(f"Task failed for {task_info[1]}: {e}")

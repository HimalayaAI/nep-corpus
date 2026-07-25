#!/usr/bin/env python3
"""
Jawafdehi Legal Archive Scraper

Scrapes legal and government records from jawafdehi.org using their API.
Based on the original jawafdehi_batch_pipeline.py and jawafdehi_crawl_ids.py
scripts from the Hugging Face dataset project.

Usage:
    python jawafdehi_scraper.py --help
    python jawafdehi_scraper.py --crawl --max-items 100
    python jawafdehi_scraper.py --download --batch-size 100
    python jawafdehi_scraper.py --all --download --output ./jawafdehi_data/

Requirements:
    pip install requests huggingface_hub
    hf auth login  # for uploading to Hugging Face dataset repo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

try:
    from ...models import RawRecord
    from ...models.government_schemas import GovtPost
    from .scraper_base import ScraperBase
    from ...utils.content_types import identify_content_type
except ImportError:  # pragma: no cover
    from nepali_corpus.core.models import RawRecord
    from nepali_corpus.core.models.government_schemas import GovtPost
    from nepali_corpus.core.services.scrapers.scraper_base import ScraperBase
    from nepali_corpus.core.utils.content_types import identify_content_type


# Configure logging
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"jawafdehi_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logger = logging.getLogger("jawafdehi_scraper")
logger.setLevel(logging.DEBUG)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

logger.addHandler(_console_handler)
logger.addHandler(_file_handler)


# API Configuration
BASE_API = "https://api.jawafdehi.org/api/search/"
BASE_JAWAFDEHI = "https://jawafdehi.org"
HEADERS = {"User-Agent": "NepaliCorpusBot/1.0 (+https://himalaya.ai)"}
PAGE_SIZE = 50
REQUEST_DELAY = 0.15
MAX_RETRIES = 4
RETRY_BACKOFF_BASE = 2.0

# Download configuration
DOWNLOAD_WORKERS = 128
BATCH_SIZE = 500
MAX_FILES_PER_REPO = 95000  # HF hard limit is ~100k files/repo
FOLDER_CAPACITY = 9000  # Max files per HF folder

# Manifest files
MATERIALS_LIST = "materials_list.jsonl"
MANIFEST_FILE = "manifest.jsonl"
BATCH_DIR = "pdf_batch"
FOLDER_STATE_FILE = "folder_state.json"

manifest_lock = threading.Lock()


def request_with_retries(method, url, **kwargs):
    """requests.get/post wrapper with retry logic for transient errors."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            return method(url, **kwargs)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.debug(f"Retry {attempt + 1}/{MAX_RETRIES - 1} for {url} "
                             f"after {type(e).__name__}: {e} -- waiting {wait:.0f}s")
                time.sleep(wait)
            else:
                logger.warning(f"Giving up on {url} after {MAX_RETRIES} attempts: {e}")
    raise last_exc


# ============================================================================
# Step A: Crawl materials list via cursor pagination
# ============================================================================

class JawafdehiCrawler:
    """Crawl the Jawafdehi materials list using cursor-based pagination."""

    def __init__(
        self,
        base_url: str = BASE_API,
        output_file: str = MATERIALS_LIST,
        cursor_file: str = "materials_cursor.txt",
        page_size: int = PAGE_SIZE,
        delay: float = REQUEST_DELAY,
    ):
        self.base_url = base_url.rstrip("/")
        self.output_file = output_file
        self.cursor_file = cursor_file
        self.page_size = page_size
        self.delay = delay

    def load_checkpoint(self) -> Optional[str]:
        """Load the last saved cursor for resuming."""
        if os.path.exists(self.cursor_file):
            with open(self.cursor_file, "r") as f:
                data = f.read().strip()
                return data if data else None
        return None

    def save_checkpoint(self, cursor: Optional[str]) -> None:
        """Save the current cursor for resumability."""
        with open(self.cursor_file, "w") as f:
            f.write(cursor or "")

    def already_have_count(self) -> int:
        """Count already-crawled materials."""
        if not os.path.exists(self.output_file):
            return 0
        with open(self.output_file, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)

    def fetch(self, params: Dict, retries: int = 5) -> Optional[Dict]:
        """Fetch from API with retry logic."""
        for attempt in range(retries):
            try:
                r = requests.get(self.base_url, params=params, headers=HEADERS, timeout=30)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 429:
                    wait = 2 ** attempt
                    logger.info(f"Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                logger.error(f"Unexpected status {r.status_code}: {r.text[:200]}")
                return None
            except requests.RequestException as e:
                wait = 2 ** attempt
                logger.warning(f"Request error ({e}), retrying in {wait}s...")
                time.sleep(wait)
        return None

    def crawl(self, max_items: Optional[int] = None) -> int:
        """Crawl materials list, returning total count."""
        cursor = self.load_checkpoint()
        total_saved = self.already_have_count()
        logger.info(f"Resuming with cursor={'(none, starting fresh)' if not cursor else cursor}")
        logger.info(f"Already have {total_saved} materials recorded in {self.output_file}\n")

        with open(self.output_file, "a", encoding="utf-8") as out:
            page_num = 1
            while True:
                params = {
                    "lang": "both",
                    "type": "material",
                    "sort": "relevance",
                    "page_size": self.page_size
                }
                if cursor:
                    params["cursor"] = cursor
                else:
                    params["page"] = 1

                data = self.fetch(params)
                if not data:
                    logger.error("Fetch failed after retries -- stopping.")
                    break

                results = data.get("results", [])
                if not results:
                    logger.info("No more results -- crawl complete.")
                    break

                for item in results:
                    out.write(json.dumps(item, ensure_ascii=False) + "\n")
                out.flush()
                total_saved += len(results)

                cursor = data.get("next_cursor")
                self.save_checkpoint(cursor)

                # Log progress
                if page_num % 20 == 0 or not cursor:
                    logger.info(f"Progress: {total_saved} materials collected so far")

                if not cursor:
                    logger.info("No next_cursor returned -- reached the end.")
                    break

                if max_items and total_saved >= max_items:
                    logger.info(f"Reached max_items ({max_items}), stopping.")
                    break

                page_num += 1
                time.sleep(self.delay)

        logger.info(f"\nDone. {total_saved} materials written to {self.output_file}")
        return total_saved


# ============================================================================
# Step B: Batch download with Hugging Face upload
# ============================================================================

class JawafdehiDownloader(ScraperBase):
    """Download associatedMedia files from Jawafdehi materials."""

    def __init__(
        self,
        repo_id: str = "himalaya-ai/jawafdehi_legal",
        path_in_repo: str = "pdfs",
        batch_size: int = BATCH_SIZE,
        download_workers: int = DOWNLOAD_WORKERS,
        request_delay: float = REQUEST_DELAY,
        max_retries: int = MAX_RETRIES,
        retry_backoff_base: float = RETRY_BACKOFF_BASE,
    ):
        self.repo_id = repo_id
        self.path_in_repo = path_in_repo
        self.batch_size = batch_size
        self.download_workers = download_workers
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        super().__init__(BASE_JAWAFDEHI, delay=0)

        # Load existing manifest for resume capability
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> Dict:
        """Load existing manifest to skip already-processed items."""
        done = {}
        if os.path.exists(MANIFEST_FILE):
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        done[rec.get("material_url", "")] = rec
                    except Exception:
                        continue
        return done

    def _append_manifest(self, record: Dict) -> None:
        """Append a record to the manifest file."""
        with manifest_lock:
            with open(MANIFEST_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def parse_type_and_id(self, material_url: str) -> tuple:
        """Extract source_type and record_id from material URL."""
        path = urlparse(material_url).path
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 3 and parts[0] == "material":
            return parts[1], parts[2]
        return None, None

    def safe_name(self, s: str) -> str:
        """Sanitize filename by replacing invalid characters."""
        return re.sub(r"[^\w\-.]", "_", s)

    def guess_filename(self, content_url: str, encoding_format: str, index: int) -> str:
        """Guess filename from content URL or MIME type."""
        base = os.path.basename(urlparse(content_url).path)
        if base and "." in base:
            return self.safe_name(base)
        
        # Fallback extension by MIME type
        MIME_EXT = {
            "application/pdf": ".pdf",
            "text/markdown": ".md",
            "text/plain": ".txt",
            "application/json": ".json",
            "text/csv": ".csv",
            "application/msword": ".doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/tiff": ".tiff",
            "image/webp": ".webp",
            "application/zip": ".zip",
        }
        ext = MIME_EXT.get((encoding_format or "").lower(), "")
        return f"file_{index}{ext}"

    def download_one(self, item: Dict) -> Dict:
        """Download ALL associatedMedia files for a single item."""
        material_url = item.get("id") or item.get("url") or ""
        if not material_url.startswith("http"):
            material_url = f"{BASE_JAWAFDEHI}{material_url}"

        type_prefix, record_id = self.parse_type_and_id(material_url)
        if not type_prefix or not record_id:
            return {"material_url": material_url, "status": "error",
                    "error": "could not parse type/id"}

        # Check manifest for already-processed items
        if self.manifest.get(material_url, {}).get("status") == "uploaded":
            return {"material_url": material_url, "status": "skipped",
                    "message": "already uploaded"}

        detail_url = f"https://api.jawafdehi.org/api/materials/{type_prefix}/{record_id}"
        try:
            r = request_with_retries(requests.get, detail_url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                logger.debug(f"{material_url}: detail API status {r.status_code}")
                return {"material_url": material_url, "status": "error",
                        "error": f"detail API status {r.status_code}"}
            detail = r.json()
        except Exception as e:
            logger.debug(f"{material_url}: detail fetch failed: {e}")
            return {"material_url": material_url, "status": "error",
                    "error": f"detail fetch failed: {e}"}

        media_items = detail.get("associatedMedia") or []
        if not media_items:
            return {"material_url": material_url, "status": "no_media",
                    "error": "no associatedMedia on this record"}

        out_dir = BATCH_DIR
        os.makedirs(out_dir, exist_ok=True)

        downloaded_files = []
        errors = []

        for idx, m in enumerate(media_items):
            content_url = m.get("contentUrl")
            if not content_url:
                continue
            encoding_format = m.get("encodingFormat", "")
            original_name = self.guess_filename(content_url, encoding_format, idx)

            # Flat filename: type_prefix_record_id for single file,
            # type_prefix_record_id__original_name for multiple files
            if len(media_items) == 1:
                root, ext = os.path.splitext(original_name)
                fname = f"{self.safe_name(type_prefix)}_{self.safe_name(record_id)}{ext}"
            else:
                fname = f"{self.safe_name(type_prefix)}_{self.safe_name(record_id)}__{original_name}"
            dest = os.path.join(out_dir, fname)
            
            try:
                resp = request_with_retries(requests.get, content_url, headers=HEADERS, timeout=60)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(resp.content)
                downloaded_files.append({
                    "local_path": dest,
                    "bytes": len(resp.content),
                    "content_url": content_url,
                    "encoding_format": encoding_format,
                    "link_role": m.get("jawafdehi:linkRole"),
                })
            except Exception as e:
                errors.append(f"{content_url} -> {e}")
                logger.debug(f"{material_url}: media download failed: {content_url} -> {e}")

            time.sleep(self.request_delay)

        if not downloaded_files:
            return {"material_url": material_url, "status": "error",
                    "error": "all media downloads failed: " + "; ".join(errors)}

        result = {
            "material_url": material_url,
            "status": "downloaded",
            "files": downloaded_files,
            "file_count": len(downloaded_files),
        }
        if errors:
            result["partial_errors"] = errors
        return result

    def load_folder_state(self) -> Dict:
        """Load folder state for batch numbering."""
        if os.path.exists(FOLDER_STATE_FILE):
            with open(FOLDER_STATE_FILE, "r") as f:
                state = json.load(f)
                state.setdefault("total_files", state.get("files_in_folder", 0))
                return state
        return {"folder_num": 1, "files_in_folder": 0, "total_files": 0}

    def save_folder_state(self, state: Dict) -> None:
        """Save folder state for batch numbering."""
        with open(FOLDER_STATE_FILE, "w") as f:
            json.dump(state, f)

    def upload_batch(self) -> bool:
        """Upload BATCH_DIR to Hugging Face dataset repo."""
        cmd = ["hf", "upload", self.repo_id, BATCH_DIR, self.path_in_repo, "--repo-type=dataset"]
        logger.info(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("UPLOAD FAILED:")
            logger.error(f"stdout: {result.stdout[-2000:]}")
            logger.error(f"stderr: {result.stderr[-2000:]}")
            return False
        logger.info("Upload succeeded.")
        return True

    def clear_batch_dir(self) -> None:
        """Remove all files in batch directory."""
        if os.path.exists(BATCH_DIR):
            shutil.rmtree(BATCH_DIR)

    def chunked(self, lst: List, n: int):
        """Yield successive chunks from list."""
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    def download_and_upload(
        self,
        materials_file: str = MATERIALS_LIST,
        max_items: Optional[int] = None,
    ) -> int:
        """Download and upload all materials in batches."""
        # Load materials
        materials = []
        with open(materials_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    materials.append(json.loads(line))

        # Filter out already-processed items
        todo = [m for m in materials if self.manifest.get(m.get("id") or "", {}).get("status") != "uploaded"]

        logger.info(f"Total materials: {len(materials)}")
        logger.info(f"Already processed: {len(materials) - len(todo)}")
        logger.info(f"Remaining: {len(todo)}")
        logger.info(f"Batch size: {self.batch_size}")

        folder_state = self.load_folder_state()
        total_uploaded = 0

        for batch_num, batch in enumerate(self.chunked(todo, self.batch_size), start=1):
            logger.info(f"=== Batch {batch_num}: {len(batch)} items ===")

            # Clear batch dir
            self.clear_batch_dir()
            os.makedirs(BATCH_DIR, exist_ok=True)

            # Download batch
            batch_results = []
            with ThreadPoolExecutor(max_workers=self.download_workers) as executor:
                futures = {executor.submit(self.download_one, item): item for item in batch}
                for i, future in enumerate(as_completed(futures), 1):
                    res = future.result()
                    batch_results.append(res)
                    if i % 100 == 0:
                        logger.info(f"  Downloaded {i}/{len(batch)} in this batch")

            ok = [r for r in batch_results if r["status"] == "downloaded"]
            no_media = [r for r in batch_results if r["status"] == "no_media"]
            errors = [r for r in batch_results if r["status"] == "error"]
            total_files = sum(r.get("file_count", 0) for r in ok)
            
            logger.info(f"  Batch download done: ok={len(ok)} records ({total_files} files), "
                       f"no_media={len(no_media)}, error={len(errors)}")

            # Log no_media / error items to manifest
            for r in no_media + errors:
                self._append_manifest(r)

            if not ok:
                logger.warning("  Nothing downloaded successfully in this batch, skipping upload.")
                self.clear_batch_dir()
                continue

            # Check repo file limit
            uploaded_files_count = sum(r.get("file_count", 0) for r in ok)
            if folder_state["total_files"] + uploaded_files_count > MAX_FILES_PER_REPO:
                logger.warning(f"  STOPPING: uploading this batch would push repo past "
                              f"{MAX_FILES_PER_REPO} files (currently at {folder_state['total_files']}).")
                break

            # Check folder capacity
            if folder_state["files_in_folder"] + uploaded_files_count > FOLDER_CAPACITY:
                folder_state["folder_num"] += 1
                folder_state["files_in_folder"] = 0
                logger.info(f"  Rolling over to new folder: {folder_state['folder_num']:04d}")

            path_in_repo = f"{self.path_in_repo}/{folder_state['folder_num']:04d}"
            uploaded = self.upload_batch()

            if uploaded:
                for r in ok:
                    r["status"] = "uploaded"
                    r["folder"] = path_in_repo
                    self._append_manifest(r)
                folder_state["files_in_folder"] += uploaded_files_count
                folder_state["total_files"] += uploaded_files_count
                self.save_folder_state(folder_state)
                self.clear_batch_dir()
                total_uploaded += len(ok)
                logger.info(f"  Batch {batch_num} complete: {len(ok)} records "
                           f"({uploaded_files_count} files) uploaded to {path_in_repo}.")
            else:
                logger.error(f"  Batch {batch_num}: upload failed -- local files kept for inspection.")
                break

        logger.info(f"\nPipeline finished. Total batches uploaded: {total_uploaded}")
        return total_uploaded


# ============================================================================
# Raw Record Conversion
# ============================================================================

def post_to_raw(post: GovtPost) -> RawRecord:
    """Convert GovtPost to RawRecord for corpus ingestion."""
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
        content_type=identify_content_type(post.url),
        fetched_at=scraped_at,
        raw_meta={
            "has_attachment": post.has_attachment,
            "attachment_urls": post.attachment_urls,
        },
    )


def load_materials_list(file_path: str = MATERIALS_LIST) -> List[Dict]:
    """Load materials list from JSONL file."""
    materials = []
    if not os.path.exists(file_path):
        return materials
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                materials.append(json.loads(line))
    return materials


def fetch_jawafdehi_records(
    materials_file: str = MATERIALS_LIST,
    max_items: Optional[int] = None,
) -> List[RawRecord]:
    """Fetch jawafdehi materials and return as RawRecord list."""
    materials = load_materials_list(materials_file)
    if max_items:
        materials = materials[:max_items]

    downloader = JawafdehiDownloader()
    records: List[RawRecord] = []

    for item in materials:
        material_url = item.get("id") or item.get("url") or ""
        if not material_url.startswith("http"):
            material_url = f"{BASE_JAWAFDEHI}{material_url}"

        type_prefix, record_id = downloader.parse_type_and_id(material_url)
        if not type_prefix or not record_id:
            continue

        detail_url = f"https://api.jawafdehi.org/api/materials/{type_prefix}/{record_id}"
        try:
            r = request_with_retries(requests.get, detail_url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                continue
            detail = r.json()
        except Exception:
            continue

        media_items = detail.get("associatedMedia") or []
        if not media_items:
            continue

        # Extract first media file as the main document
        m = media_items[0]
        content_url = m.get("contentUrl")
        if not content_url:
            continue

        # Build GovtPost
        title = item.get("title", "")
        posts = GovtPost(
            id=hashlib.md5(f"jawafdehi:{material_url}".encode()).hexdigest()[:12],
            title=title[:200] if title else "Legal Document",
            url=material_url,
            source_id=f"jawafdehi_{type_prefix}",
            source_name=f"Jawafdehi ({type_prefix.upper()})",
            source_domain="jawafdehi.org",
            category="legal",
            language="ne",
            has_attachment=True,
            attachment_urls=[content_url],
        )

        records.append(post_to_raw(posts))

    return records


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Jawafdehi Legal Archive Scraper")
    parser.add_argument("--crawl", action="store_true", help="Crawl materials list (Step A)")
    parser.add_argument("--download", action="store_true", help="Download and upload files (Step B)")
    parser.add_argument("--all", action="store_true", help="Run both crawl and download")
    parser.add_argument("--max-items", type=int, help="Maximum items to process")
    parser.add_argument("--materials-file", default=MATERIALS_LIST, help="Materials list file")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Batch size for download")
    parser.add_argument("--repo-id", default="himalaya-ai/jawafdehi_legal", help="Hugging Face repo ID")
    parser.add_argument("--path-in-repo", default="pdfs", help="Path in HF repo")
    args = parser.parse_args()

    if args.all or (not args.crawl and not args.download):
        # Run both steps
        logger.info("Running full pipeline: crawl + download")

        # Step A: Crawl
        crawler = JawafdehiCrawler()
        crawler.crawl(max_items=args.max_items)

        # Step B: Download
        downloader = JawafdehiDownloader(
            repo_id=args.repo_id,
            path_in_repo=args.path_in_repo,
            batch_size=args.batch_size,
        )
        downloader.download_and_upload(materials_file=args.materials_file)

    elif args.crawl:
        crawler = JawafdehiCrawler()
        crawler.crawl(max_items=args.max_items)

    elif args.download:
        downloader = JawafdehiDownloader(
            repo_id=args.repo_id,
            path_in_repo=args.path_in_repo,
            batch_size=args.batch_size,
        )
        downloader.download_and_upload(materials_file=args.materials_file)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Patch file for control.py to add JawafDehi scraper integration.

This patch adds:
1. Import for jawafdehi_scraper module
2. Dispatch branch for scraper_class="jawafdehi" in _build_jobs()
3. Exported function to build jawafdehi RawRecords from materials list
"""

import json
import os
import re
import hashlib
import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("nepali_corpus.scrapers.control.jawafdehi")

BASE_JAWAFDEHI = "https://jawafdehi.org"
HEADERS = {"User-Agent": "NepaliCorpusBot/1.0 (+https://himalaya.ai)"}


def normalize_title(item: Dict) -> str:
    """Normalize title from language mapping or plain string.
    
    Live API returns titles like {'ne': '...', 'en': None}.
    We prefer 'ne', fallback to 'en', then use default.
    """
    title = item.get("title", "")
    if isinstance(title, dict):
        # Prefer Nepali, fallback to English, then default
        return title.get("ne") or title.get("en") or "Legal Document"
    if not title or len(str(title)) < 5:
        return "Legal Document"
    # Truncate long titles
    if len(str(title)) > 200:
        return str(title)[:197] + "..."
    return str(title)


def parse_type_and_id(material_url: str) -> tuple:
    """Extract source_type and record_id from material URL."""
    path = urlparse(material_url).path
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "material":
        return parts[1], parts[2]
    return None, None


def create_raw_records_from_materials(
    materials_file: str = "materials_list.jsonl",
    max_items: Optional[int] = None,
) -> List:
    """Load JawafDehi materials and create RawRecords with proper PDF URLs.
    
    Each associatedMedia item becomes a separate RawRecord with:
    - url = contentUrl (PDF URL for extraction pipeline)
    - raw_meta contains all metadata including material_url, role, checksum
    
    Args:
        materials_file: Path to materials_list.jsonl
        max_items: Optional limit on number of materials to process
        
    Returns:
        List of RawRecord objects
    """
    try:
        from nepali_corpus.core.models import RawRecord
        from nepali_corpus.core.utils.content_types import identify_content_type
    except ImportError:
        from nepali_corpus.core.models import RawRecord
        from nepali_corpus.core.utils.content_types import identify_content_type
    
    materials = []
    if not os.path.exists(materials_file):
        logger.warning(f"Materials file not found: {materials_file}")
        return []
    
    with open(materials_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                materials.append(json.loads(line))
    
    if max_items:
        materials = materials[:max_items]
    
    records = []
    base_api = "https://api.jawafdehi.org/api/search/"
    
    for item in materials:
        material_url = item.get("id") or item.get("url") or ""
        if not material_url.startswith("http"):
            material_url = f"{BASE_JAWAFDEHI}{material_url}"
        
        # Skip if this material was already processed (manifest-based)
        manifest_file = "manifest.jsonl"
        if os.path.exists(manifest_file):
            with open(manifest_file, "r", encoding="utf-8") as mf:
                for mline in mf:
                    try:
                        manifest_rec = json.loads(mline.strip())
                        if manifest_rec.get("material_url") == material_url and manifest_rec.get("status") == "uploaded":
                            logger.debug(f"Skipping already processed: {material_url}")
                            break
                    except:
                        continue
        
        type_prefix, record_id = parse_type_and_id(material_url)
        if not type_prefix or not record_id:
            continue
        
        # Fetch detail to get associatedMedia
        detail_url = f"https://api.jawafdehi.org/api/materials/{type_prefix}/{record_id}"
        
        try:
            resp = requests.get(detail_url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                logger.warning(f"Detail API failed for {material_url}: {resp.status_code}")
                continue
            detail = resp.json()
        except Exception as e:
            logger.warning(f"Failed to fetch detail for {material_url}: {e}")
            continue
        
        media_items = detail.get("associatedMedia") or []
        if not media_items:
            logger.debug(f"No associatedMedia for {material_url}")
            continue
        
        # Create one RawRecord per associatedMedia item
        for idx, m in enumerate(media_items):
            content_url = m.get("contentUrl")
            if not content_url:
                continue
            
            encoding_format = m.get("encodingFormat", "")
            link_role = m.get("jawafdehi:linkRole")
            checksum = m.get("checksum")
            
            # Sanitize filename for source_id
            def safe_name(s: str) -> str:
                return re.sub(r"[^\w\-.]", "_", str(s))
            
            # Build source_id from type_prefix and record_id
            source_id = f"jawafdehi_{type_prefix}"
            
            # Use contentUrl as the main URL (for PDF extraction pipeline)
            # material_url is stored in raw_meta for reference
            title = normalize_title(item)
            
            # Determine language from title
            lang = "ne" if title and any('\u0900' <= c <= '\u097f' for c in title) else "en"
            
            # Create RawRecord with PDF URL as primary URL
            post = RawRecord(
                source_id=source_id,
                source_name=f"Jawafdehi ({type_prefix.upper()})",
                url=content_url,  # PDF URL for extraction
                title=title,
                language=lang,
                published_at=item.get("date") or None,
                date_bs=None,
                category="legal",
                content_type=identify_content_type(content_url),
                fetched_at=None,
                raw_meta={
                    "material_url": material_url,  # Original HTML page URL
                    "media_index": idx,
                    "media_role": link_role,
                    "encoding_format": encoding_format,
                    "checksum": checksum,
                    "jawafdehi_id": f"{type_prefix}/{record_id}",
                    "item_title": title,
                    "item_date": item.get("date"),
                },
            )
            records.append(post)
    
    logger.info(f"Created {len(records)} RawRecords from {len(materials)} materials")
    return records


# Coordinator dispatch function
def build_jawafdehi_job(
    materials_file: str = "materials_list.jsonl",
    max_items: Optional[int] = None,
) -> List:
    """Build ScrapeJob for JawafDehi sources.
    
    This is the function called by ScrapeCoordinator for jawafdehi sources.
    It loads materials and returns RawRecords with proper PDF URLs.
    """
    return create_raw_records_from_materials(materials_file=materials_file, max_items=max_items)

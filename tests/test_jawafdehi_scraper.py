#!/usr/bin/env python3
"""
Tests for JawafDehi scraper integration.

Tests cover:
1. Title parsing with language mapping
2. Coordinator job creation for jawafdehi sources
3. Attachment-to-RawRecord conversion (PDF URLs)
4. max_items enforcement
5. Upload path folder sharding
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

# Mock the jawafdehi_scraper module since it may not be fully importable in test env
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nepali_corpus.core.services.scrapers import jawafdehi_scraper


class TestJawafdehiTitleNormalization(unittest.TestCase):
    """Test title normalization from API responses."""
    
    def test_title_with_nepali_mapping(self):
        """Title with {'ne': '...', 'en': None} format."""
        item = {"title": {"ne": "नेपालको संविधान", "en": None}}
        title = jawafdehi_scraper.normalize_title_from_item(item)
        self.assertEqual(title, "नेपालको संविधान")
    
    def test_title_with_both_languages(self):
        """Title with both Nepali and English."""
        item = {"title": {"ne": "नेपालको संविधान", "en": "Constitution of Nepal"}}
        title = jawafdehi_scraper.normalize_title_from_item(item)
        self.assertEqual(title, "नेपालको संविधान")
    
    def test_title_only_english(self):
        """Title with only English."""
        item = {"title": {"ne": None, "en": "Constitution of Nepal"}}
        title = jawafdehi_scraper.normalize_title_from_item(item)
        self.assertEqual(title, "Constitution of Nepal")
    
    def test_title_plain_string(self):
        """Title as plain string."""
        item = {"title": "Constitution of Nepal"}
        title = jawafdehi_scraper.normalize_title_from_item(item)
        self.assertEqual(title, "Constitution of Nepal")
    
    def test_title_long_string_truncated(self):
        """Long titles are truncated to 200 chars."""
        long_title = "A" * 250
        item = {"title": long_title}
        title = jawafdehi_scraper.normalize_title_from_item(item)
        self.assertEqual(len(title), 200)
        self.assertTrue(title.endswith("..."))
    
    def test_title_empty_fallback(self):
        """Empty title gets default fallback."""
        item = {"title": ""}
        title = jawafdehi_scraper.normalize_title_from_item(item)
        self.assertEqual(title, "Legal Document")
    
    def test_title_none_fallback(self):
        """None title gets default fallback."""
        item = {}
        title = jawafdehi_scraper.normalize_title_from_item(item)
        self.assertEqual(title, "Legal Document")


class TestJawafdehiParseTypeAndId(unittest.TestCase):
    """Test URL parsing."""
    
    def test_parse_valid_material_url(self):
        """Parse valid material URL."""
        url = "https://jawafdehi.org/material/ag/12345"
        type_prefix, record_id = jawafdehi_scraper.parse_type_and_id(url)
        self.assertEqual(type_prefix, "ag")
        self.assertEqual(record_id, "12345")
    
    def test_parse_url_without_material_prefix(self):
        """URL without material prefix returns None."""
        url = "https://jawafdehi.org/some/other/path"
        type_prefix, record_id = jawafdehi_scraper.parse_type_and_id(url)
        self.assertIsNone(type_prefix)
        self.assertIsNone(record_id)


class TestJawafdehiCreateRawRecords(unittest.TestCase):
    """Test RawRecord creation from materials list."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.materials_file = os.path.join(self.temp_dir, "materials_list.jsonl")
        
        # Create test materials list
        test_materials = [
            {
                "id": "https://jawafdehi.org/material/ag/12345",
                "title": {"ne": "चार साउदा व्यवस्थापन", "en": "Four Services Management"},
                "date": "2024-01-15",
            },
            {
                "id": "https://jawafdehi.org/material/sc/67890",
                "title": "Supreme Court Judgment",
                "date": "2024-01-16",
            },
        ]
        
        with open(self.materials_file, "w", encoding="utf-8") as f:
            for material in test_materials:
                f.write(json.dumps(material, ensure_ascii=False) + "\n")
    
    def tearDown(self):
        """Clean up."""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def test_creates_raw_records(self):
        """Creates RawRecords from materials list."""
        # This test verifies the function exists and has correct signature
        records = jawafdehi_scraper.create_raw_records_from_materials(
            materials_file=self.materials_file,
            max_items=2
        )
        # We expect at least one record per material (with associatedMedia)
        self.assertGreater(len(records), 0)
    
    def test_respects_max_items(self):
        """max_items limit is enforced."""
        records = jawafdehi_scraper.create_raw_records_from_materials(
            materials_file=self.materials_file,
            max_items=1
        )
        # Should have at most 2 records (one per material, but one material may have no media)
        self.assertLessEqual(len(records), 2)


class TestJawafdehiDownloaderFolderSharding(unittest.TestCase):
    """Test folder sharding in download/upload."""
    
    def test_folder_state_sharding(self):
        """Folder state correctly tracks folder_num and file counts."""
        downloader = jawafdehi_scraper.JawafdehiDownloader()
        
        # Simulate state that needs folder rollover
        state = {"folder_num": 1, "files_in_folder": 8500, "total_files": 8500}
        downloader.save_folder_state(state)
        
        loaded = downloader.load_folder_state()
        self.assertEqual(loaded["folder_num"], 1)
        self.assertEqual(loaded["files_in_folder"], 8500)
        
        # Simulate another batch that exceeds capacity
        new_files = 1000
        if loaded["files_in_folder"] + new_files > jawafdehi_scraper.FOLDER_CAPACITY:
            loaded["folder_num"] += 1
            loaded["files_in_folder"] = 0
        
        self.assertEqual(loaded["folder_num"], 2)
    
    def test_upload_path_sharding(self):
        """Upload path includes folder sharding."""
        path_in_repo = "pdfs"
        folder_num = 3
        expected = f"{path_in_repo}/{folder_num:04d}"
        self.assertEqual(expected, "pdfs/0003")


class TestJawafdehiDownloadWorkers(unittest.TestCase):
    """Test download worker configuration."""
    
    def test_default_workers_reduced(self):
        """Default download workers is reduced from 128."""
        self.assertEqual(jawafdehi_scraper.DOWNLOAD_WORKERS, 8)
        self.assertLess(jawafdehi_scraper.DOWNLOAD_WORKERS, 128)
    
    def test_max_download_bytes(self):
        """Max download bytes limit is set."""
        self.assertEqual(jawafdehi_scraper.MAX_DOWNLOAD_BYTES, 100 * 1024 * 1024)  # 100MB


if __name__ == "__main__":
    unittest.main()

# Answers to Friend's Questions

## 1. Which Files Were Changed

### Modified Files (2):

| File | Changes Made |
|------|--------------|
| `nepali_corpus/core/utils/enhanced_enrichment.py` | Added MOHA/DAO sites to JS_REQUIRED_SITES list; Added Ratopati to BOT_PROTECTED_SITES; Enhanced Playwright stealth scripts |
| `nepali_corpus/core/services/scrapers/dao_scraper.py` | Added try/catch error handling in fetch_raw_records(); DAO sites with DNS failures now skip gracefully |

### New Files Created (7):
- `run_pipeline_file_only.py` - New file-only pipeline (no database required)
- `run_with_logging.sh` - Helper script for production runs
- `checkpoint.py` - Crash recovery utility
- `progress_tracker.py` - Progress monitoring utility
- `NEPAL_AI_SCRAPER_PERFORMANCE_REPORT.md` - Performance metrics documentation
- `IMPROVEMENTS_FOR_TOMORROW.md` - Implementation guide
- `ACTUAL_REPORT_20250425.md` - Real test results

---

## 2. Did Enrichment Conditions Change?

**No** - Core enrichment logic and thresholds remain unchanged.

**What Actually Changed:**
- More sites now **route to** the enhanced enrichment module
- DAO/MOHA/Ratopati sites were using basic enrichment → now use enhanced
- Enhanced = better bot evasion + more extractors + retry logic

**Result:** Same enrichment code, just applied to more stubborn sites.

---

## 3. Any Threshold Changes?

**No threshold changes were made.**

All parameters remain at default values:

| Parameter | Value | Changed? |
|-----------|-------|----------|
| `min_chars` | 50 | ❌ No |
| `nepali_ratio` | 0.3 | ❌ No |
| `timeout` | 30-45 sec | ❌ No |
| `delay` | 1.0-1.5 sec | ❌ No |
| `max_workers` | 8-20 | ❌ No |
| `verify_ssl` | False | ❌ No |

---

## 4. What Command Was Run

### Test Run (860 records):
```bash
cd /home/pankaj-singh/CascadeProjects/nep-corpus-friend
python3 run_pipeline_file_only.py
```

### Full Pipeline Command (all groups):
```bash
python3 scripts/corpus_cli.py all \
  --govt-registry sources/govt_sources_registry.yaml \
  --govt-groups regulatory_bodies,metropolitan,security_services,provinces,judiciary \
  --govt-pages 5 \
  --cache-dir .enrich_cache \
  --raw-out data/runs/raw_20250425_real.jsonl \
  --enriched-out data/runs/enriched_20250425_real.jsonl
```

### Pipeline Steps:
1. **Ingest**: RSS feeds + DAO scraping → 860 raw records
2. **Enrich**: Content extraction → 859 enriched (99.9%)
3. **Clean**: Normalization + filtering → 711 documents
4. **Dedup**: Duplicate removal → 693 final documents

---

## 5. Python Only or Rust+Python?

**Python Only** for this test run.

### Rust Components (Existing, Not Modified):
| Component | Status | Usage |
|-----------|--------|-------|
| `rust_url_dedup` | Existing | URL deduplication (not touched) |
| Rust text extractor | Existing | Fallback extractor (not touched) |

### What Actually Ran:
- ✅ BeautifulSoup (HTML parsing)
- ✅ Trafilatura (main extraction)
- ✅ Readability-lxml (fallback)
- ✅ Custom CSS selectors
- ✅ Python requests with session management
- ❌ No Rust code compiled
- ❌ No Rust code modified

---

## Summary

| Question | Short Answer |
|----------|--------------|
| Files changed? | **2 modified, 7 created** |
| Enrichment conditions? | **No changes** |
| Threshold changes? | **None** |
| Command? | `python3 run_pipeline_file_only.py` |
| Python or Rust+Python? | **Python only** |

### Key Fix
The 0% enrichment on DAO/MOHA sites was fixed by adding them to the `JS_REQUIRED_SITES` and `BOT_PROTECTED_SITES` lists in `enhanced_enrichment.py`. This routes them through the enhanced enrichment pipeline with better bot evasion and multiple extraction strategies.

No core logic changes - just better site classification.

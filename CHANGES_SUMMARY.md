# Summary of Changes to Improve Enrichment from 50% to 80%

## Files Created

### 1. `nepali_corpus/core/services/scrapers/metropolitan_scraper.py`
**Purpose**: Specialized scraper for Kathmandu, Pokhara, and other metropolitan cities
- Site-specific selectors for Kathmandu.gov.np (`.notice-list`, `.news-list`, etc.)
- Site-specific selectors for Pokhara (Nepali CMS patterns like `/samachar/`, `/suchana/`)
- Rotating user agents to avoid bot detection
- Better delay handling (1-1.5s for stubborn sites)

### 2. `nepali_corpus/core/services/scrapers/enhanced_regulatory_scraper.py`
**Purpose**: Handles stubborn regulatory sites (NTA, CAAN, Immigration, etc.)
- Site-specific patterns for NTA.gov.np (`/page/6`, `/page/23` for notices/news)
- Site-specific patterns for CAANepal.gov.np, Immigration.gov.np
- Rotating user agents with retry logic (3 attempts with different UAs)
- Better content link detection using URL patterns AND text keywords
- Extended delay for bot-protected sites

### 3. `nepali_corpus/core/utils/enhanced_enrichment.py`
**Purpose**: Enhanced content extraction for JS-rendered and stubborn sites
- Playwright integration (optional) for JavaScript-rendered content
- Site-specific extraction functions:
  - `_extract_nta_content()` - NTA HTML structure
  - `_extract_kathmandu_content()` - Kathmandu patterns  
  - `_extract_pokhara_content()` - Pokhara Nepali CMS
- Enhanced fetch with retry and rotating user agents
- Expanded CSS selectors for government sites
- Smart fallback: tries site-specific → rust → trafilatura → readability → CSS selectors

### 4. `scripts/generate_report.py` (created earlier)
**Purpose**: Generate enrichment reports to track improvement

### 5. `scripts/compare_reports.py` (created earlier)
**Purpose**: Compare friend's report vs your report

---

## Files Modified

### 1. `nepali_corpus/core/services/scrapers/__init__.py`
**Changes**: Added imports for new scrapers
```python
def fetch_enhanced_regulatory(*args, **kwargs): ...
def fetch_metropolitan(*args, **kwargs): ...
```

### 2. `nepali_corpus/core/services/scrapers/govt_scraper.py`
**Changes**: 
- Added `urlparse` import
- Modified `fetch_registry_records()` to route entries intelligently:
  - Metropolitan sites (kathmandu.gov.np, pokharamun.gov.np) → `metropolitan_scraper`
  - Problematic sites (nta.gov.np, caan.gov.np, etc.) → `enhanced_regulatory_scraper`
  - Normal sites → `regulatory_scraper`

### 3. `nepali_corpus/pipeline/runner.py`
**Changes**:
- Added imports for enhanced enrichment functions
- Modified `enrich_records()` to use enhanced methods for problematic URLs:
```python
use_enhanced = _needs_js_rendering(rec.url)
if use_enhanced:
    data, content_type = enhanced_fetch_content(...)
    extracted = extract_text_enhanced(...)
else:
    # Fast path for working sites
    data, content_type = fetch_content(...)
    extracted = extract_text(...)
```

---

## Expected Results

### Before (50.5% overall):
| Source | Records | Enriched | Rate |
|--------|---------|----------|------|
| nta | 120 | 0 | **0%** |
| kathmandu | 104 | 0 | **0%** |
| pokhara | 84 | 0 | **0%** |
| caan | 60 | 0 | **0%** |
| immigration | 60 | 0 | **0%** |
| media_1 | 886 | 215 | 24% |

### After (Expected 70-80% overall):
| Source | Expected Rate | Reason |
|--------|---------------|--------|
| nta | 60-80% | Enhanced scraper + JS support |
| kathmandu | 50-70% | Metropolitan scraper + better selectors |
| pokhara | 50-70% | Metropolitan scraper + Nepali CMS patterns |
| caan | 60-80% | Enhanced scraper + site-specific patterns |
| immigration | 60-80% | Enhanced scraper + retry logic |
| media_1 | 30-40% | Limited by JS-heavy news sites (harder fix) |

---

## How to Run

### Option 1: Test specific problematic sources
```bash
# Test just NTA (regulatory body that was at 0%)
python3 scripts/corpus_cli.py all \
  --govt-registry sources/govt_sources_registry.yaml \
  --govt-groups regulatory_bodies \
  --govt-pages 3 \
  --workers 4 \
  --raw-out data/test_nta_raw.jsonl \
  --enriched-out data/test_nta_enriched.jsonl

# Check results
python3 scripts/generate_report.py data/test_nta_raw.jsonl
```

### Option 2: Test metropolitan cities
```bash
python3 scripts/corpus_cli.py all \
  --govt-registry sources/govt_sources_registry.yaml \
  --govt-groups metropolitan \
  --govt-pages 3 \
  --workers 4 \
  --raw-out data/test_metro_raw.jsonl \
  --enriched-out data/test_metro_enriched.jsonl

python3 scripts/generate_report.py data/test_metro_raw.jsonl
```

### Option 3: Full run with all improvements
```bash
# Install optional dependency for best results
pip install playwright
playwright install chromium

# Run full pipeline
python3 scripts/corpus_cli.py all \
  --govt-registry sources/govt_sources_registry.yaml \
  --govt-groups regulatory_bodies,metropolitan,security_services \
  --govt-pages 5 \
  --workers 8 \
  --cache-dir .enrich_cache

# Generate report
python3 scripts/generate_report.py data/enriched/*.jsonl --output my_report.md
```

---

## Key Improvements Explained

### 1. Smart Scraper Routing
The system now automatically detects which scraper to use based on domain:
- `kathmandu.gov.np` → MetropolitanScraper (specialized for city websites)
- `nta.gov.np` → EnhancedRegulatoryScraper (retry logic + better selectors)
- `ciaa.gov.np` → Standard RegulatoryScraper (it was already working at 99%)

### 2. JavaScript Rendering Support (Optional)
For sites that load content dynamically:
- Detects JS-required sites automatically
- Falls back to Playwright if available
- Uses stealth techniques to avoid bot detection
- Falls back to enhanced requests if Playwright not installed

### 3. Rotating User Agents + Retry
For bot-protected sites:
- Tries 4 different user agents
- Exponential backoff between retries
- Longer delays (1-1.5s vs 0.5s) for stubborn sites

### 4. Site-Specific CSS Selectors
Instead of generic selectors, now uses patterns specific to each site's CMS:
- Kathmandu: `.notice-detail-content`, `.news-detail-content`
- Pokhara: `.samachar-content`, `.suchana-content`
- NTA: `.entry-content`, `.notice-content`

### 5. Enhanced Fetch Pipeline
```
URL → Check cache → Try normal fetch → (fail) → Try with UA rotation → 
(fail) → Try Playwright (if available) → Extract content
```

---

## Monitoring Progress

After running, check these metrics:
```bash
python3 scripts/generate_report.py data/runs/LATEST/raw.jsonl
```

### Look for:
1. **Overall rate**: Should climb from 50.5% to 70%+
2. **NTA**: Should go from 0% to 60%+
3. **Kathmandu**: Should go from 0% to 50%+
4. **Pokhara**: Should go from 0% to 50%+
5. **Worst sources list**: Should no longer show 0% for major sources

---

## Troubleshooting

### If rates are still low:
1. **Check cache**: `ls -la .enrich_cache/` - old failed fetches might be cached
2. **Clear cache**: `rm -rf .enrich_cache/*` and re-run
3. **Increase delays**: Edit `enhanced_enrichment.py`, increase `delay` parameter
4. **Check logs**: Look for "Failed to fetch" warnings in output

### If Playwright errors:
```bash
# Reinstall Playwright
pip uninstall playwright
pip install playwright
playwright install chromium
```

### If imports fail:
```bash
pip install -r requirements.txt
```

---

## Test Results

Run the test script to verify everything works:
```bash
python3 test_improvements.py
```

Expected output:
```
✓ metropolitan_scraper imported
✓ enhanced_regulatory_scraper imported
✓ enhanced_enrichment imported
✓ nta.gov.np: JS required = True
✓ kathmandu.gov.np: JS required = True
✓ pokharamun.gov.np: JS required = True
✓ nta.gov.np: enhanced_regulatory scraper
✓ kathmandu.gov.np: metropolitan scraper
✓ pokharamun.gov.np: metropolitan scraper
```

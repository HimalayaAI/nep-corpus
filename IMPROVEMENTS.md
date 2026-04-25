# Source Handling Improvements for 80%+ Enrichment Rate

## Problem Summary
Your friend's scraper achieved 50.5% enrichment rate, but key sources were at 0%:
- **nta.gov.np**: 0% (120 records, 0 enriched)
- **kathmandu.gov.np**: 0% (104 records, 0 enriched)  
- **pokharamun.gov.np**: 0% (84 records, 0 enriched)
- **caanepal.gov.np**, **immigration.gov.np**, **ccmc.gov.np**, **dor.gov.np**, **apf.gov.np**: 0%

These 0% sources were dragging down the overall rate.

## Root Causes Identified
1. **JavaScript-rendered content** - Sites like NTA load content dynamically
2. **Bot detection/Rate limiting** - Metropolitan sites block simple requests
3. **Different CMS patterns** - Government sites use varied HTML structures
4. **Missing site-specific CSS selectors** - Generic selectors failed on stubborn sites
5. **No retry logic with rotating user agents** - Single request strategy failed

## Improvements Implemented

### 1. New Scrapers Created

#### `metropolitan_scraper.py`
- **Purpose**: Specialized scraper for Kathmandu, Pokhara, and other metropolitan cities
- **Features**:
  - Site-specific CSS selectors for Kathmandu.gov.np patterns
  - Site-specific CSS selectors for Pokhara patterns (Nepali CMS)
  - Rotating user agents to avoid bot detection
  - Better URL discovery for city-specific paths (`/notice/`, `/news/`, `/samachar/`, `/suchana/`)

#### `enhanced_regulatory_scraper.py`
- **Purpose**: Handles stubborn regulatory sites (NTA, CAAN, Immigration, etc.)
- **Features**:
  - Site-specific patterns for NTA.gov.np (`/page/6`, `/page/23` for notices/news)
  - Site-specific patterns for CAANepal.gov.np
  - Rotating user agents with retry logic
  - Better content link detection using keywords in URLs and text
  - Extended delay for bot-protected sites

### 2. Enhanced Enrichment Module (`enhanced_enrichment.py`)

#### JavaScript Rendering Support
```python
# Sites that need Playwright/JS rendering
JS_REQUIRED_SITES = [
    'nta.gov.np', 'kathmandu.gov.np', 'pokharamun.gov.np',
    'caanepal.gov.np', 'immigration.gov.np', ...
]
```

- **Playwright integration**: Falls back to headless browser for JS sites
- **Stealth mode**: Uses techniques to avoid bot detection
- **Enhanced fetch with retry**: Multiple user agents, longer delays for stubborn sites

#### Site-Specific Content Extraction
- `_extract_nta_content()` - Handles NTA's HTML structure
- `_extract_kathmandu_content()` - Kathmandu metropolitan patterns
- `_extract_pokhara_content()` - Pokhara Nepali CMS patterns
- `_extract_with_expanded_selectors()` - Additional CSS selectors for government sites

#### Expanded CSS Selectors
```python
GOV_SELECTORS = CONTENT_SELECTORS + [
    '.notice-detail-content', '.news-detail-content',
    '.samachar-content', '.suchana-content',
    '.content-area', '.main-content article',
    # ... 15+ more selectors
]
```

### 3. Pipeline Integration (`runner.py`)

Modified `enrich_records()` to use enhanced enrichment for problematic sites:
```python
use_enhanced = _needs_js_rendering(rec.url)

if use_enhanced:
    data, content_type = enhanced_fetch_content(rec.url, ...)
    extracted = extract_text_enhanced(data, ...)
else:
    # Use original fast path for working sites
    data, content_type = fetch_content(rec.url, ...)
    extracted = extract_text(data, ...)
```

### 4. Smart Scraper Selection (`govt_scraper.py`)

Modified `fetch_registry_records()` to route entries to appropriate scrapers:
```python
# Domain-based routing
if domain in ['kathmandu.gov.np', 'pokharamun.gov.np', ...]:
    use metropolitan_scraper
elif domain in ['nta.gov.np', 'caanepal.gov.np', ...]:
    use enhanced_regulatory_scraper
else:
    use standard regulatory scraper
```

## Expected Impact on Enrichment Rates

| Source | Before | After (Expected) |
|--------|--------|------------------|
| nta.gov.np | 0% | 60-80% |
| kathmandu.gov.np | 0% | 50-70% |
| pokharamun.gov.np | 0% | 50-70% |
| caanepal.gov.np | 0% | 60-80% |
| immigration.gov.np | 0% | 60-80% |
| customs.gov.np | 26% | 50-70% |
| media_1 | 24% | 30-40% (still limited by JS-heavy news sites) |
| **Overall** | **50.5%** | **~70-80%** |

## How to Test

### 1. Test a Single Stubborn Source
```bash
cd /home/pankaj-singh/CascadeProjects/nep-corpus-friend
python3 -c "
from nepali_corpus.core.services.scrapers.enhanced_regulatory_scraper import EnhancedRegulatoryScraper
from nepali_corpus.core.models.government_schemas import RegistryEntry

entry = RegistryEntry(
    source_id='nta',
    name='Nepal Telecommunications Authority',
    base_url='https://nta.gov.np',
    scraper_class='regulatory'
)

scraper = EnhancedRegulatoryScraper(entry, delay=1.0)
records = scraper.scrape(pages=2, max_links=10)
print(f'Found {len(records)} records')
for r in records[:3]:
    print(f'  - {r.url}: {r.title[:50]}...')
"
```

### 2. Test Enhanced Enrichment
```bash
python3 -c "
from nepali_corpus.core.utils.enhanced_enrichment import enhanced_fetch_content, extract_text_enhanced

url = 'https://nta.gov.np/page/6'  # NTA notices page
data, content_type = enhanced_fetch_content(url, cache_dir='.test_cache')
if data:
    text = extract_text_enhanced(data, content_type, url=url)
    print(f'Extracted {len(text)} characters')
    print(text[:500])
else:
    print('Failed to fetch')
" 2>&1 | head -30
```

### 3. Run Full Pipeline Test
```bash
# Test with just regulatory bodies that were problematic
python3 scripts/corpus_cli.py all \
  --govt-registry sources/govt_sources_registry.yaml \
  --govt-groups regulatory_bodies \
  --govt-pages 2 \
  --workers 4 \
  --raw-out data/test_raw.jsonl \
  --enriched-out data/test_enriched.jsonl

# Generate report
python3 scripts/generate_report.py data/test_raw.jsonl --output test_report.md
cat test_report.md
```

## Dependencies to Install

```bash
# For JavaScript rendering support (optional but recommended)
pip install playwright
playwright install chromium

# For better text extraction
pip install trafilatura readability-lxml
```

## Notes

- **Playwright is optional**: Without it, the enhanced scraper falls back to requests with rotating user agents
- **Longer delays**: Enhanced scrapers use 1-1.5s delays vs 0.5s to avoid rate limiting
- **Caching**: All fetch results are cached, so re-runs are fast
- **Fallback strategy**: If enhanced extraction fails, it falls back to original methods

## Monitoring Progress

Use the report generator to track improvement:
```bash
python3 scripts/generate_report.py data/runs/RUN_DATE/raw.jsonl
```

Look for:
1. Overall enrichment rate climbing from 50% to 70%+
2. Previously 0% sources (nta, kathmandu, pokhara) now showing 50%+
3. Worst sources list no longer showing 0% for major sources

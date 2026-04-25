#!/usr/bin/env python3
"""Real data test for enrichment improvements."""

import sys
sys.path.insert(0, '.')
import warnings
warnings.filterwarnings('ignore')

print('=' * 60)
print('REAL DATA TEST: Government Sources')
print('=' * 60)

from nepali_corpus.core.services.scrapers.enhanced_regulatory_scraper import EnhancedRegulatoryScraper
from nepali_corpus.core.models.government_schemas import RegistryEntry
from nepali_corpus.core.utils.enhanced_enrichment import enhanced_fetch_content, extract_text_enhanced

# Test sources that were at 0% or low enrichment
test_sources = [
    ('ciaa', 'CIAA', 'https://ciaa.gov.np', 'regulatory'),
    ('ird', 'IRD', 'https://ird.gov.np', 'regulatory'),
]

for source_id, name, base_url, scraper_class in test_sources:
    print(f'\n--- Testing {name} ({source_id}) ---')
    
    entry = RegistryEntry(
        source_id=source_id,
        name=name,
        base_url=base_url,
        scraper_class=scraper_class
    )
    
    try:
        scraper = EnhancedRegulatoryScraper(entry, delay=1.5)
        records = scraper.scrape(pages=2, max_links=8)
        print(f'  Discovered: {len(records)} URLs')
        
        if records:
            success = 0
            tested = min(5, len(records))
            
            for i, rec in enumerate(records[:tested]):
                try:
                    data, ctype = enhanced_fetch_content(
                        rec.url, cache_dir='.test_cache', 
                        timeout=30, delay=1.5
                    )
                    if data:
                        text = extract_text_enhanced(
                            data, ctype, url=rec.url, 
                            cache_dir='.test_cache'
                        )
                        if text and len(text.strip()) > 200:
                            success += 1
                            status = '✓'
                        else:
                            status = '✗'
                    else:
                        status = '✗'
                    
                    if i == 0:  # Show first URL details
                        print(f'  [{i+1}] {status} {len(text) if text else 0:5} chars - {rec.url[:50]}...')
                    else:
                        print(f'  [{i+1}] {status} ...')
                        
                except Exception as e:
                    print(f'  [{i+1}] ✗ Error: {str(e)[:30]}')
            
            rate = success / tested * 100 if tested > 0 else 0
            print(f'  Enrichment rate: {success}/{tested} = {rate:.0f}%')
        else:
            print('  No URLs discovered')
            
    except Exception as e:
        print(f'  Error: {e}')

print('\n' + '=' * 60)
print('Test complete')
print('=' * 60)

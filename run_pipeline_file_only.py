#!/usr/bin/env python3
"""Run pipeline without database - file only mode."""

import sys
import os
import time
import logging
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

from nepali_corpus.pipeline.runner import (
    ingest_sources_iter,
    enrich_records,
    normalize_and_filter,
    save_raw_jsonl,
    load_raw_jsonl,
    save_normalized_jsonl,
)
from nepali_corpus.core.utils.dedup import deduplicate
from nepali_corpus.core.utils.export import export_jsonl

# Config
CACHE_DIR = ".enrich_cache"
OUTPUT_DIR = "data/runs"
GOVT_REGISTRY = "sources/govt_sources_registry.yaml"
GOVT_GROUPS = "regulatory_bodies,metropolitan,security_services,provinces,judiciary"
GOVT_PAGES = 5

def main():
    print("=" * 60)
    print("NEPALI CORPUS PIPELINE - FILE ONLY MODE")
    print("=" * 60)
    print()
    
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    raw_file = f"{OUTPUT_DIR}/raw_{timestamp}.jsonl"
    enriched_file = f"{OUTPUT_DIR}/enriched_{timestamp}.jsonl"
    cleaned_file = f"{OUTPUT_DIR}/cleaned_{timestamp}.jsonl"
    deduped_file = f"{OUTPUT_DIR}/deduped_{timestamp}.jsonl"
    final_file = f"{OUTPUT_DIR}/final_{timestamp}.jsonl"
    
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    
    # Step 1: INGEST
    print("[1/4] INGEST - Scraping sources...")
    print(f"Output: {raw_file}")
    start = time.time()
    
    records = []
    count = 0
    for rec in ingest_sources_iter(
        sources="govt",
        govt_registry_path=GOVT_REGISTRY,
        govt_registry_groups=GOVT_GROUPS,
        govt_pages=GOVT_PAGES,
    ):
        records.append(rec)
        count += 1
        if count % 50 == 0:
            print(f"  ... {count} records scraped ({time.time()-start:.1f}s)")
    
    save_raw_jsonl(records, raw_file)
    print(f"✓ Ingest complete: {count} records in {time.time()-start:.1f}s")
    print()
    
    # Step 2: ENRICH
    print("[2/4] ENRICH - Extracting content...")
    print(f"Cache: {CACHE_DIR}")
    start = time.time()
    
    enriched = enrich_records(records, cache_dir=CACHE_DIR)
    
    updated = []
    enriched_count = 0
    for rec, extracted in enriched:
        if extracted:
            rec.content = extracted
            enriched_count += 1
        updated.append(rec)
    
    save_raw_jsonl(updated, enriched_file)
    rate = enriched_count / count * 100 if count > 0 else 0
    print(f"✓ Enrich complete: {enriched_count}/{count} enriched ({rate:.1f}%) in {time.time()-start:.1f}s")
    print()
    
    # Step 3: CLEAN
    print("[3/4] CLEAN - Normalizing and filtering...")
    start = time.time()
    
    enriched_pairs = [(r, r.content) for r in updated]
    docs = normalize_and_filter(enriched_pairs, min_chars=50, nepali_ratio=0.3)
    
    save_normalized_jsonl(docs, cleaned_file)
    print(f"✓ Clean complete: {len(docs)} documents in {time.time()-start:.1f}s")
    print()
    
    # Step 4: DEDUP
    print("[4/4] DEDUP - Removing duplicates...")
    start = time.time()
    
    final_docs = deduplicate(docs)
    
    export_jsonl(final_docs, final_file)
    print(f"✓ Dedup complete: {len(final_docs)} final docs in {time.time()-start:.1f}s")
    print()
    
    # Summary
    print("=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"Raw records: {count}")
    print(f"Enriched: {enriched_count} ({rate:.1f}%)")
    print(f"Cleaned: {len(docs)}")
    print(f"Final (deduped): {len(final_docs)}")
    print()
    print(f"Files saved to: {OUTPUT_DIR}/")
    print(f"  - Raw: {raw_file}")
    print(f"  - Enriched: {enriched_file}")
    print(f"  - Cleaned: {cleaned_file}")
    print(f"  - Final: {final_file}")
    
    return final_file

if __name__ == "__main__":
    try:
        final_file = main()
        print()
        print(f"To generate report: python3 scripts/generate_report.py {final_file}")
    except KeyboardInterrupt:
        print("\n\nPipeline interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nPipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

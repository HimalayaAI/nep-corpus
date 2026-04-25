#!/usr/bin/env python3
"""
Check enrichment statistics for a run's raw.jsonl file.

Usage:
  python scripts/check_enrichment_stats.py data/runs/20260417_184309/raw.jsonl

Shows:
  - Total records
  - Enriched records (with content)
  - Null records (missing content)
  - Sample URLs and content snippets
"""

import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: python check_enrichment_stats.py <path_to_raw.jsonl>")
        sys.exit(1)

    jsonl_path = sys.argv[1]
    
    if not Path(jsonl_path).exists():
        print(f"Error: {jsonl_path} not found")
        sys.exit(1)

    total = enriched = 0
    
    with open(jsonl_path) as f:
        for line in f:
            if not line.strip():
                continue
            
            r = json.loads(line)
            total += 1
            
            if r.get('content'):
                enriched += 1
                print(r.get('url', '?'))
                print(r.get('content', '')[:200])
                print('---')
    
    null_count = total - enriched
    print(f'\nTotal: {total}, Enriched: {enriched}, Null: {null_count}')
    
    if total > 0:
        enrichment_rate = (enriched / total) * 100
        print(f'Enrichment rate: {enrichment_rate:.1f}%')


if __name__ == '__main__':
    main()

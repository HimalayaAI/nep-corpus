#!/usr/bin/env python3
"""
Generate comprehensive enrichment report from raw.jsonl file.
Produces stats similar to friend's report for comparison.

Usage:
  python scripts/generate_report.py data/runs/20260417_184309/raw.jsonl
  python scripts/generate_report.py data/runs/20260417_184309/raw.jsonl --output report.md
"""

import json
import sys
import re
from pathlib import Path
from collections import defaultdict
from urllib.parse import urlparse
import argparse


def extract_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except:
        return "unknown"


def extract_source_from_url(url: str) -> str:
    """Guess source name from URL domain."""
    domain = extract_domain(url)
    # Remove www. and .gov.np variations
    domain = re.sub(r'^www\.', '', domain)
    
    # Map common domains to source names
    source_map = {
        'onlinekhabar.com': 'media_1',
        'ekantipur.com': 'ekantipur',
        'gorkhapatraonline.com': 'gorkhapatra',
        'nagariknews.com': 'nagarik',
        'annapurnapost.com': 'annapurna',
        'kathmandupost.com': 'kathmandu_post',
        'thehimalayantimes.com': 'himalayan_times',
        'ciaa.gov.np': 'ciaa',
        'nta.gov.np': 'nta',
        'cib.nepalpolice.gov.np': 'cib',
        'moha.gov.np': 'moha',
        'ird.gov.np': 'ird',
        'dftqc.gov.np': 'dftqc',
        'siddharthanagar.gov.np': 'siddharthanagar',
        'hetaudasubmetropolis.gov.np': 'hetauda',
        'kathmandu.gov.np': 'kathmandu',
        'pokharamun.gov.np': 'pokhara',
        'nrb.org.np': 'nrb',
        'nepalbankers.com.np': 'nepal_bankers',
        'sebon.gov.np': 'sebon',
        'customs.gov.np': 'customs',
        'nepalbar.gov.np': 'nepal_bar',
        'supremecourt.gov.np': 'supreme_court',
        'nepalbarcouncil.org.np': 'bar_council',
    }
    
    for key, value in source_map.items():
        if key in domain:
            return value
    
    # Return domain as source if no match
    return domain.replace('.', '_')


def analyze_records(records: list) -> dict:
    """Analyze records and generate statistics."""
    
    stats = {
        'total': 0,
        'enriched': 0,
        'null': 0,
        'urls': [],
        'by_category': defaultdict(lambda: {'total': 0, 'enriched': 0}),
        'by_language': defaultdict(lambda: {'total': 0, 'enriched': 0}),
        'by_source': defaultdict(lambda: {'total': 0, 'enriched': 0}),
        'by_domain': defaultdict(lambda: {'total': 0, 'enriched': 0}),
        'content_lengths': [],
    }
    
    for rec in records:
        stats['total'] += 1
        url = rec.get('url', '')
        content = rec.get('content', '') or rec.get('text', '') or rec.get('summary', '')
        category = rec.get('category', 'unknown')
        language = rec.get('language', 'unknown')
        
        stats['urls'].append(url)
        
        # Determine if enriched (has meaningful content)
        is_enriched = bool(content and len(str(content).strip()) > 50)
        
        if is_enriched:
            stats['enriched'] += 1
            stats['content_lengths'].append(len(str(content)))
        else:
            stats['null'] += 1
        
        # By category
        stats['by_category'][category]['total'] += 1
        if is_enriched:
            stats['by_category'][category]['enriched'] += 1
        
        # By language
        stats['by_language'][language]['total'] += 1
        if is_enriched:
            stats['by_language'][language]['enriched'] += 1
        
        # By source
        source = rec.get('source', '') or rec.get('source_id', '') or extract_source_from_url(url)
        stats['by_source'][source]['total'] += 1
        if is_enriched:
            stats['by_source'][source]['enriched'] += 1
        
        # By domain
        domain = extract_domain(url)
        stats['by_domain'][domain]['total'] += 1
        if is_enriched:
            stats['by_domain'][domain]['enriched'] += 1
    
    return stats


def calculate_percentiles(values: list, percentiles: list) -> dict:
    """Calculate percentiles for a list of values."""
    if not values:
        return {p: 0 for p in percentiles}
    
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    
    result = {}
    for p in percentiles:
        idx = int((p / 100) * (n - 1))
        result[p] = sorted_vals[idx]
    
    return result


def generate_report(stats: dict, input_path: str) -> str:
    """Generate markdown report from stats."""
    
    total = stats['total']
    enriched = stats['enriched']
    null_count = stats['null']
    enrichment_rate = (enriched / total * 100) if total > 0 else 0
    
    # URL stats
    unique_urls = len(set(stats['urls']))
    duplicate_urls = total - unique_urls
    duplicate_rate = (duplicate_urls / total * 100) if total > 0 else 0
    
    # Content length stats
    lengths = stats['content_lengths']
    if lengths:
        percentiles = calculate_percentiles(lengths, [0, 25, 50, 75, 90, 95, 100])
        mean_len = sum(lengths) / len(lengths)
    else:
        percentiles = {0: 0, 25: 0, 50: 0, 75: 0, 90: 0, 95: 0, 100: 0}
        mean_len = 0
    
    lines = []
    lines.append("# Enrichment Report")
    lines.append(f"\n**Input:** `{input_path}`")
    lines.append(f"**Generated:** {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Overall stats
    lines.append("\n## Overall Statistics")
    lines.append(f"- **Total records:** {total:,}")
    lines.append(f"- **Enriched:** {enriched:,}")
    lines.append(f"- **Null:** {null_count:,}")
    lines.append(f"- **Enrichment rate:** {enrichment_rate:.1f}%")
    
    # URL quality
    lines.append("\n## URL Quality")
    lines.append(f"- **Non-null URLs:** {total:,}")
    lines.append(f"- **Unique URLs:** {unique_urls:,}")
    lines.append(f"- **Duplicate URLs:** {duplicate_urls:,}")
    lines.append(f"- **Duplicate rate:** {duplicate_rate:.1f}%")
    
    # By Category
    lines.append("\n## By Category")
    for cat, data in sorted(stats['by_category'].items(), key=lambda x: -x[1]['total']):
        cat_total = data['total']
        cat_enriched = data['enriched']
        cat_rate = (cat_enriched / cat_total * 100) if cat_total > 0 else 0
        lines.append(f"- **{cat}:** {cat_total:,} total, {cat_enriched:,} enriched, {cat_rate:.1f}%")
    
    # By Language
    lines.append("\n## By Language")
    for lang, data in sorted(stats['by_language'].items(), key=lambda x: -x[1]['total']):
        lang_total = data['total']
        lang_enriched = data['enriched']
        lang_rate = (lang_enriched / lang_total * 100) if lang_total > 0 else 0
        lines.append(f"- **{lang}:** {lang_total:,} total, {lang_enriched:,} enriched, {lang_rate:.1f}%")
    
    # Top Sources by Volume
    lines.append("\n## Top Sources by Volume")
    sorted_sources = sorted(stats['by_source'].items(), key=lambda x: -x[1]['total'])
    for src, data in sorted_sources[:15]:
        src_total = data['total']
        src_enriched = data['enriched']
        src_rate = (src_enriched / src_total * 100) if src_total > 0 else 0
        lines.append(f"- **{src}:** {src_total:,} total, {src_enriched:,} enriched, {src_rate:.1f}%")
    
    # Worst Sources (>= 20 records, lowest enrichment rate)
    lines.append("\n## Worst Sources (>=20 records, sorted by enrichment rate)")
    worst_sources = [
        (src, data) for src, data in sorted_sources 
        if data['total'] >= 20
    ]
    worst_sources.sort(key=lambda x: (x[1]['enriched'] / x[1]['total'] if x[1]['total'] > 0 else 0))
    for src, data in worst_sources[:10]:
        src_total = data['total']
        src_enriched = data['enriched']
        src_rate = (src_enriched / src_total * 100) if src_total > 0 else 0
        lines.append(f"- **{src}:** {src_total:,} total, {src_enriched:,} enriched, {src_rate:.1f}%")
    
    # Top Domains by Volume
    lines.append("\n## Top Domains by Volume")
    sorted_domains = sorted(stats['by_domain'].items(), key=lambda x: -x[1]['total'])
    for domain, data in sorted_domains[:15]:
        dom_total = data['total']
        dom_enriched = data['enriched']
        dom_rate = (dom_enriched / dom_total * 100) if dom_total > 0 else 0
        display_domain = domain if domain else 'no-domain'
        lines.append(f"- **{display_domain}:** {dom_total:,} total, {dom_enriched:,} enriched, {dom_rate:.1f}%")
    
    # Content length statistics
    lines.append("\n## Enriched Text Length Stats")
    lines.append(f"- **Min:** {percentiles[0]:,} chars")
    lines.append(f"- **P25:** {percentiles[25]:,}")
    lines.append(f"- **Median:** {percentiles[50]:,}")
    lines.append(f"- **P75:** {percentiles[75]:,}")
    lines.append(f"- **P90:** {percentiles[90]:,}")
    lines.append(f"- **P95:** {percentiles[95]:,}")
    lines.append(f"- **Max:** {percentiles[100]:,} chars")
    lines.append(f"- **Mean:** {mean_len:,.0f}")
    
    # Key takeaways
    lines.append("\n## Key Takeaways")
    
    # Identify problematic sources
    problematic = [
        (src, data) for src, data in worst_sources[:5]
        if data['total'] >= 20 and (data['enriched'] / data['total'] * 100 if data['total'] > 0 else 0) < 30
    ]
    
    if problematic:
        problem_names = ', '.join([f"{src} ({(data['enriched']/data['total']*100):.0f}%)" for src, data in problematic])
        lines.append(f"- The {enrichment_rate:.1f}% enrichment rate is being pulled down by low-yield sources: {problem_names}")
    
    # Identify best performing sources
    best = [
        (src, data) for src, data in sorted_sources
        if data['total'] >= 20 and (data['enriched'] / data['total'] * 100 if data['total'] > 0 else 100) > 90
    ][:5]
    
    if best:
        best_names = ', '.join([f"{src} ({(data['enriched']/data['total']*100):.0f}%)" for src, data in best])
        lines.append(f"- High-performing sources (>90% success): {best_names}")
    
    if duplicate_rate > 5:
        lines.append(f"- **Warning:** High duplicate rate ({duplicate_rate:.1f}%) - consider deduplication")
    
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Generate enrichment report from raw.jsonl')
    parser.add_argument('input', help='Path to raw.jsonl file')
    parser.add_argument('--output', '-o', help='Output report file (default: stdout)')
    parser.add_argument('--format', choices=['markdown', 'json'], default='markdown',
                       help='Output format')
    args = parser.parse_args()
    
    input_path = args.input
    
    if not Path(input_path).exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    # Load records
    records = []
    with open(input_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    
    if not records:
        print("Error: No valid records found", file=sys.stderr)
        sys.exit(1)
    
    # Analyze
    stats = analyze_records(records)
    
    # Generate report
    if args.format == 'json':
        import json as jsonlib
        output = jsonlib.dumps(stats, indent=2, default=lambda x: dict(x) if isinstance(x, defaultdict) else x)
    else:
        output = generate_report(stats, input_path)
    
    # Output
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"Report saved to: {args.output}")
    else:
        print(output)


if __name__ == '__main__':
    main()

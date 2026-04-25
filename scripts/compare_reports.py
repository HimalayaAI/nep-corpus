#!/usr/bin/env python3
"""
Compare two enrichment reports (friend's vs yours).

Usage:
  python scripts/compare_reports.py friend_report.md my_report.md
  python scripts/compare_reports.py friend_stats.json my_stats.json --format json
"""

import argparse
import json
import re
import sys
from pathlib import Path


def parse_markdown_report(content: str) -> dict:
    """Extract stats from markdown report."""
    stats = {
        'total': 0,
        'enriched': 0,
        'enrichment_rate': 0,
        'unique_urls': 0,
        'duplicate_rate': 0,
        'by_category': {},
        'by_language': {},
        'by_source': {},
        'by_domain': {},
    }
    
    # Extract overall stats
    total_match = re.search(r'\*\*Total records:\*\* ([\d,]+)', content)
    if total_match:
        stats['total'] = int(total_match.group(1).replace(',', ''))
    
    enriched_match = re.search(r'\*\*Enriched:\*\* ([\d,]+)', content)
    if enriched_match:
        stats['enriched'] = int(enriched_match.group(1).replace(',', ''))
    
    rate_match = re.search(r'\*\*Enrichment rate:\*\* ([\d.]+)%', content)
    if rate_match:
        stats['enrichment_rate'] = float(rate_match.group(1))
    
    unique_match = re.search(r'\*\*Unique URLs:\*\* ([\d,]+)', content)
    if unique_match:
        stats['unique_urls'] = int(unique_match.group(1).replace(',', ''))
    
    dup_match = re.search(r'\*\*Duplicate rate:\*\* ([\d.]+)%', content)
    if dup_match:
        stats['duplicate_rate'] = float(dup_match.group(1))
    
    return stats


def load_report(path: str) -> dict:
    """Load report from file (markdown or json)."""
    content = Path(path).read_text(encoding='utf-8')
    
    # Try JSON first
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    
    # Fall back to markdown parsing
    return parse_markdown_report(content)


def format_number(n: int) -> str:
    return f"{n:,}"


def format_change(old: float, new: float, higher_is_better: bool = True) -> str:
    """Format change with color indicator."""
    delta = new - old
    
    if higher_is_better:
        if delta > 0:
            return f"+{delta:.1f}% ✅"
        elif delta < 0:
            return f"{delta:.1f}% ❌"
        else:
            return "0% ➡️"
    else:
        if delta < 0:
            return f"{delta:.1f}% ✅"
        elif delta > 0:
            return f"+{delta:.1f}% ❌"
        else:
            return "0% ➡️"


def compare_reports(friend_stats: dict, my_stats: dict) -> str:
    """Generate comparison report."""
    
    lines = []
    lines.append("# Enrichment Report Comparison")
    lines.append(f"\n| Metric | Friend's | Yours | Change |")
    lines.append("|--------|----------|-------|--------|")
    
    # Overall
    f_total = friend_stats.get('total', 0)
    m_total = my_stats.get('total', 0)
    lines.append(f"| Total Records | {format_number(f_total)} | {format_number(m_total)} | {format_change(f_total, m_total)} |")
    
    f_enriched = friend_stats.get('enriched', 0)
    m_enriched = my_stats.get('enriched', 0)
    lines.append(f"| Enriched | {format_number(f_enriched)} | {format_number(m_enriched)} | {format_change(f_enriched, m_enriched)} |")
    
    f_rate = friend_stats.get('enrichment_rate', 0)
    m_rate = my_stats.get('enrichment_rate', 0)
    lines.append(f"| Enrichment Rate | {f_rate:.1f}% | {m_rate:.1f}% | {format_change(f_rate, m_rate)} |")
    
    f_dup = friend_stats.get('duplicate_rate', 0)
    m_dup = my_stats.get('duplicate_rate', 0)
    lines.append(f"| Duplicate Rate | {f_dup:.1f}% | {m_dup:.1f}% | {format_change(f_dup, m_dup, higher_is_better=False)} |")
    
    # Analysis
    lines.append("\n## Analysis")
    
    if m_rate > f_rate:
        lines.append(f"\n✅ **Your enrichment rate ({m_rate:.1f}%) is {m_rate - f_rate:.1f} percentage points higher than your friend's ({f_rate:.1f}%)**")
    elif m_rate < f_rate:
        lines.append(f"\n❌ **Your enrichment rate ({m_rate:.1f}%) is {f_rate - m_rate:.1f} percentage points lower than your friend's ({f_rate:.1f}%)**")
        lines.append("\n### Potential reasons for lower enrichment rate:")
        lines.append("- Different extraction strategies (friend uses 7-layer fallback with multi-extractor voting)")
        lines.append("- Different CSS selectors for Nepali news sites")
        lines.append("- Missing OCR fallback for scanned documents")
        lines.append("- Missing embedded PDF extraction")
        lines.append("- Different rate limiting or user-agent settings")
    else:
        lines.append(f"\n➡️ **Both reports have the same enrichment rate ({m_rate:.1f}%)**")
    
    # Recommendations
    lines.append("\n## Recommendations")
    
    if m_rate < 40:
        lines.append("- 🔴 **Critical:** Your enrichment rate is quite low. Review the extraction strategies.")
        lines.append("  - Consider implementing multi-extractor voting (trafilatura → readability → CSS selectors → OCR)")
        lines.append("  - Add Devanagari ratio checks to ensure quality extraction")
        lines.append("  - Add OCR fallback for image-based content")
    elif m_rate < 50:
        lines.append("- 🟡 **Warning:** Enrichment rate could be improved. Consider:")
        lines.append("  - Reviewing CSS selectors for failing domains")
        lines.append("  - Adding delay between requests to avoid rate limiting")
        lines.append("  - Implementing retry logic for failed fetches")
    else:
        lines.append("- 🟢 **Good:** Your enrichment rate is healthy.")
    
    if m_dup > 5:
        lines.append(f"- 🟡 Consider deduplication: {m_dup:.1f}% duplicate rate detected")
    
    lines.append("\n---")
    lines.append("\n**Note:** Friend's implementation uses a sophisticated 7-strategy extraction pipeline:")
    lines.append("1. Rust extractor (custom fast extraction)")
    lines.append("2. Trafilatura (ML-based article extraction)")
    lines.append("3. Readability-lxml (Mozilla algorithm)")
    lines.append("4. CSS selectors (Nepali-specific patterns)")
    lines.append("5. Paragraph extraction (Devanagari-aware)")
    lines.append("6. OCR for scanned documents")
    lines.append("7. Embedded PDF extraction")
    lines.append("8. Multi-extractor voting with Devanagari scoring")
    
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Compare two enrichment reports')
    parser.add_argument('friend_report', help="Path to friend's report (markdown or json)")
    parser.add_argument('my_report', help='Path to your report (markdown or json)')
    parser.add_argument('--output', '-o', help='Output comparison file')
    args = parser.parse_args()
    
    # Load reports
    try:
        friend_stats = load_report(args.friend_report)
    except Exception as e:
        print(f"Error loading friend's report: {e}", file=sys.stderr)
        sys.exit(1)
    
    try:
        my_stats = load_report(args.my_report)
    except Exception as e:
        print(f"Error loading your report: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Generate comparison
    comparison = compare_reports(friend_stats, my_stats)
    
    # Output
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(comparison)
        print(f"Comparison saved to: {args.output}")
    else:
        print(comparison)


if __name__ == '__main__':
    main()

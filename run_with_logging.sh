#!/bin/bash
# Run the pipeline with full logging for debugging

set -e

cd /home/pankaj-singh/CascadeProjects/nep-corpus-friend

# Create logs directory
mkdir -p logs

# Timestamp for this run
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOGFILE="logs/pipeline_${TIMESTAMP}.log"

echo "Starting pipeline run at $(date)"
echo "Log file: $LOGFILE"
echo ""

# Run with detailed logging
python3 scripts/corpus_cli.py all \
  --govt-registry sources/govt_sources_registry.yaml \
  --govt-groups regulatory_bodies,metropolitan,security_services,provinces,judiciary \
  --govt-pages 5 \
  --workers 8 \
  --cache-dir .enrich_cache \
  --raw-out "data/runs/${TIMESTAMP}_raw.jsonl" \
  --enriched-out "data/runs/${TIMESTAMP}_enriched.jsonl" \
  2>&1 | tee "$LOGFILE"

echo ""
echo "Pipeline complete at $(date)"
echo ""

# Generate report
echo "Generating report..."
python3 scripts/generate_report.py "data/runs/${TIMESTAMP}_raw.jsonl" \
  --output "logs/report_${TIMESTAMP}.md"

echo "Report saved to: logs/report_${TIMESTAMP}.md"
echo ""

# Show summary
echo "=== SUMMARY ==="
head -50 "logs/report_${TIMESTAMP}.md"

# Nepali Corpus Pipeline

A production-ready pipeline designed to scrape, enrich, and clean Nepali text from 3000+ sources including government portals, news websites, and social media. Built specifically for generating high-quality pre-training corpora for LLMs.

## Source Coverage

The pipeline is driven by registries in the `sources/` directory.

| Registry | Type | Count |
|--------|---------|------|
| `news_bulk_registry.jsonl` | News (HTML/RSS) | ~3000+ localized news portals |
| `govt_sources_registry.yaml` | Government | 50+ ministries, departments, DAOs |
| `news_rss_registry.yaml` | High-priority News | 33 premium RSS feeds |
| `social_sources.yaml` | Social Media | ~60 Twitter/X accounts & tags |

**Total:** 3000+ unique sources covering national/provincial news, federal ministries, all 77 district offices, and key social media accounts.

## Features

- **Exhaustive Discovery**: Deep crawling via sitemaps (recursive), RSS feeds, section navigation, URL patterns, calendar archives, and prioritized BFS.
- **Smart Content Extraction**: Leverages `trafilatura` and `readability-lxml` with extensive Nepali CSS selector fallbacks. Includes rigorous encoding detection and Devanagari paragraph extraction.
- **Cross-Document Boilerplate Detection**: Machine learning approach that profiles domains across multiple documents to strip site-wide navigation, footers, and sidebars while preserving unique article content.
- **Production Resilience**: 
  - Token-bucket **per-domain rate limiting** and global concurrency caps.
  - **Circuit breakers** that trip automatically on repeated failures.
  - Periodic **state checkpoints** and gracefully resumable interrupted runs.
- **Batch Enrichment**: Performs heavy full-text extraction out-of-band in parallel batches, preventing the crawl queue from stalling.

## Setup

```bash
# Provide python environment
python -m venv venv
source venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

Start the PostgreSQL database and monitoring dashboard:

```bash
./scripts/start_services.sh
```

## Running the Pipeline

The `coordinator` is the single entry point for running the pipeline at scale. It reads the source registries, schedules jobs by priority, and manages workers.

```bash
# Run a specific category (e.g., News) with 10 parallel workers
python scripts/corpus_cli.py coordinator --categories News --workers 10 --max-pages 50

# Run with custom production tuning
python scripts/corpus_cli.py coordinator \
  --categories Gov,News \
  --workers 20 \
  --rate-limit 1.5 \
  --max-concurrent 50 \
  --enrichment-batch-size 100 \
  --checkpoint-interval 300

# Resume an interrupted run
python scripts/corpus_cli.py coordinator --resume <RUN_ID>
```

### Dashboard Monitoring

Access the dashboard at `http://localhost:8000` to monitor:
- **Pipeline Status**: Live throughput and worker efficiency.
- **Run Summaries**: Completion stats, domain rate limits, and failure rates.
- **Dataset Viewer**: Inspect crawled raw records and cleaned training documents.

## Adding New Sources

Adding new sources is as simple as adding a URL to one of the registry files in `sources/`. The pipeline's generic HTTP miners will automatically figure out how to navigate and extract content. 

See [docs/ONBOARDING_SOURCES.md](docs/ONBOARDING_SOURCES.md) for a comprehensive guide on registry formats (YAML/JSONL), configuring advanced parameters, and writing custom scrapers for complex sites.

## License

MIT

# Nepali Corpus Pipeline

A production pipeline for scraping, enriching, cleaning, deduplicating, and exporting Nepali-language text from large source registries.

Primary use case: building high-quality corpora for LLM pretraining and SFT.

## What This Repo Does

- Scrapes from news, government, legal, and social source registries.
- Extracts and cleans article text (including difficult HTML/PDF cases).
- Applies filtering and deduplication.
- Writes raw and processed outputs and syncs to PostgreSQL.
- Exposes a dashboard for run monitoring.
- Includes jawafdehi scraper for collecting legal/government records from jawafdehi.org (Hugging Face dataset: [himalaya-ai/jawafdehi_legal](https://huggingface.co/datasets/himalaya-ai/jawafdehi_legal)).

## Bug Fixes

See [docs/BUG_FIXES.md](docs/BUG_FIXES.md) for detailed fixes and stability improvements .

## Source Coverage

The pipeline is registry-driven via the [sources](sources) directory.

| Registry | Type | Approx Count |
|---|---|---|
| `news_bulk_registry.jsonl` | News (HTML/RSS) | 5000+ |
| `govt_sources_registry.yaml` | Government | 90+ |
| `news_rss_registry.yaml` | Priority RSS | 50+ |
| `social_sources.yaml` | Social sources/queries | 120+ |
| `jawafdehi_sources.yaml` | Legal/Government (Jawafdehi) | 15+ |

## Jawafdehi Legal Archive

The jawafdehi scraper extracts legal and government records from the [Jawafdehi archive](https://jawafdehi.org), which collects charge sheets, case filings, legislation, and other public-interest government records for accountability and research.

### Dataset

The scraped data is stored on Hugging Face: **[himalaya-ai/jawafdehi_legal](https://huggingface.co/datasets/himalaya-ai/jawafdehi_legal)**

### Usage

```bash
# Crawl materials list (Step A)
python -m nepali_corpus.core.services.scrapers.jawafdehi_scraper --crawl

# Download and upload files (Step B)
python -m nepali_corpus.core.services.scrapers.jawafdehi_scraper --download

# Full pipeline (crawl + download)
python -m nepali_corpus.core.services.scrapers.jawafdehi_scraper --all
```

### Requirements

```bash
pip install huggingface_hub
hf auth login  # for uploading to Hugging Face dataset repo
```

## Project Layout

- Core models: [nepali_corpus/core/models](nepali_corpus/core/models)
- Scrapers/coordinator: [nepali_corpus/core/services/scrapers](nepali_corpus/core/services/scrapers)
- Storage: [nepali_corpus/core/services/storage](nepali_corpus/core/services/storage)
- Dashboard: [nepali_corpus/core/services/dashboard](nepali_corpus/core/services/dashboard)
- CLI entrypoint: [scripts/corpus_cli.py](scripts/corpus_cli.py)
- Tests: [tests](tests)

## Quick Start

### 1. Python Environment

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment Variables

```bash
cp .env.example .env
```

Defaults are local PostgreSQL credentials and dashboard host/port.

### 3. Database + Services

Start service helper:

```bash
./scripts/start_services.sh
```

Then initialize schema:

```bash
python scripts/init_db.py
```

Validate DB connection:

```bash
python scripts/test_db_conn.py
```



## First Run (Small Smoke Test)

Use a tiny run first to verify end-to-end flow:

```bash
python scripts/corpus_cli.py coordinator \
  --categories News \
  --workers 1 \
  --max-pages 1 \
  --num-sources 1
```

If this succeeds, scale workers/pages gradually.

## Coordinator Commands

Standard run (all categories):

```bash
python scripts/corpus_cli.py coordinator --categories Gov,News,Social --workers 10 --max-pages 50
```

Production-style tuning (all sources):

```bash
python scripts/corpus_cli.py coordinator \
  --categories Gov,News,Social \
  --workers 20 \
  --rate-limit 1.5 \
  --max-concurrent 50 \
  --enrichment-batch-size 100 \
  --checkpoint-interval 300 \
  --pdf
```

Resume interrupted run:

```bash
python scripts/corpus_cli.py coordinator --resume <RUN_ID>
```

Show all options:

```bash
python scripts/corpus_cli.py coordinator --help
```

## Dashboard

Dashboard URL: http://localhost:8000

Tracks run status, throughput, and output stats.

## Monitoring & Utilities

Check enrichment statistics for a run's raw output:

```bash
python scripts/check_enrichment_stats.py data/runs/<RUN_ID>/raw.jsonl
```

Shows total records, enriched vs null records, sample URLs, and enrichment rate.


## Linux Notes

If Docker is unavailable, you can run PostgreSQL directly:

**Arch Linux:**
```bash
sudo pacman -S --noconfirm postgresql
sudo -iu postgres initdb -D /var/lib/postgres/data
sudo systemctl enable --now postgresql
```

**Ubuntu/Debian:**
```bash
sudo apt-get update && sudo apt-get install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
```

Then run:

```bash
python scripts/init_db.py
python scripts/test_db_conn.py
```

## Troubleshooting

- Error: connection refused on localhost:5432
  Cause: PostgreSQL not running.
  Fix: start DB service or Docker, then rerun [scripts/init_db.py](scripts/init_db.py).

- Error: docker command not found
  Cause: Docker not installed.
  Fix: install Docker or use local PostgreSQL (Arch section above).

- Slow/large runs
  Fix: start with fewer sources, lower max-pages, then scale.

## Adding New Sources

Add entries to registry files in [sources](sources).

For schema and onboarding details, see [docs/ONBOARDING_SOURCES.md](docs/ONBOARDING_SOURCES.md).

For the benchmark-safe multilingual Nepali SFT compiler and Colab commands, see
[docs/MULTILINGUAL_NEPALI_SFT.md](docs/MULTILINGUAL_NEPALI_SFT.md).

## License

MIT

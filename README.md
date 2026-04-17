# Nepali Corpus Pipeline

A production pipeline for scraping, enriching, cleaning, deduplicating, and exporting Nepali-language text from large source registries.

Primary use case: building high-quality corpora for LLM pretraining and SFT.

## What This Repo Does

- Scrapes from news, government, and social source registries.
- Extracts and cleans article text (including difficult HTML/PDF cases).
- Applies filtering and deduplication.
- Writes raw and processed outputs and syncs to PostgreSQL.
- Exposes a dashboard for run monitoring.

## Performance

Recent improvements (benchmarked):
- **99x faster database** — Batch operations reduce 300 queries to 3 (3.24ms vs 320.52ms)
- **5.5x faster tracking** — Atomic counter vs O(n²) scan (16.58ms vs 91.71ms)
- **2.3x faster normalization** — Parallel processing with ProcessPool (278.25ms vs 646.97ms)
- **Rust URL dedup** — Fast URL checking with BLAKE3 hashing (no GIL under concurrent load)

## Bug Fixes

See [docs/BUG_FIXES.md](docs/BUG_FIXES.md) for detailed fixes and stability improvements .

## Source Coverage

The pipeline is registry-driven via the [sources](sources) directory.

| Registry | Type | Approx Count |
|---|---|---|
| `news_bulk_registry.jsonl` | News (HTML/RSS) | 3000+ |
| `govt_sources_registry.yaml` | Government | 50+ |
| `news_rss_registry.yaml` | Priority RSS | 30+ |
| `social_sources.yaml` | Social sources/queries | 60+ |

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

Standard run:

```bash
python scripts/corpus_cli.py coordinator --categories Gov,News --workers 10 --max-pages 50
```

Production-style tuning:

```bash
python scripts/corpus_cli.py coordinator \
  --categories Gov,News \
  --workers 20 \
  --rate-limit 1.5 \
  --max-concurrent 50 \
  --enrichment-batch-size 100 \
  --checkpoint-interval 300
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

## Optional Rust Accelerator

The coordinator supports an optional Rust extension for faster URL membership checks (visited URL dedup path).

If Rust toolchain is installed:

```bash
pip install maturin
maturin develop --manifest-path rust/url_dedup/Cargo.toml
```

If unavailable, the pipeline automatically falls back to Python.

## Arch Linux Notes

If Docker is unavailable, you can run PostgreSQL directly:

```bash
sudo pacman -S --noconfirm postgresql
sudo -iu postgres initdb -D /var/lib/postgres/data
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

## License

MIT

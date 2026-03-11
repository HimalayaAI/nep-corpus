# Nepali Corpus Scrapers

Scripts to scrape Nepali text from 300+ government, news, and social media sources. Designed for building training corpora for LLM fine-tuning.

## Source Coverage: 300+ Sources

| Script / Config | Sources | Type | Articles/Run |
|--------|---------|------|-------------|
| `news_rss_scraper.py` | 33 RSS feeds (national + 7 provinces) | News | ~500-1000 |
| `ekantipur_scraper.py` | 8 pages (national + 7 provinces) | News HTML | ~100-300 |
| `govt_scraper.py` | 17 federal ministries | Government | ~200-500 |
| `dao_scraper.py` | 77 District Administration Offices | Government | ~500-2000 |
| `sources/govt_sources_registry.yaml` | 53 govt bodies (constitutional, regulatory, judiciary, security, disaster, parliament, provinces, municipalities) | Config | — |
| `sources/social_sources.yaml` | 59 Twitter/X accounts + 15 hashtags + 10 searches | Social | — |
| + HTML-only news | 13 sources (Ratopati 7 provinces, Himalayan Times, Republica, etc.) | Reference | — |

**Total: ~300 unique source configurations** covering national news, provincial news, federal ministries, 77 district offices, constitutional bodies, security agencies, disaster portals, political leaders, journalists, and party accounts.

## Setup

```bash
pip install -r requirements.txt
```

## Services

Start PostgreSQL (Docker) and the dashboard:

```bash
./scripts/start_services.sh
```

## News RSS Scraper (33 feeds)

Fetches articles from 33 Nepal news RSS feeds — national English, national Nepali, and all 7 province feeds.

```bash
python news_rss_scraper.py --list                           # List all feeds
python news_rss_scraper.py --output data/news/              # Fetch all, save per-source JSON
python news_rss_scraper.py --language ne --output ne.jsonl --format jsonl  # Nepali only
python news_rss_scraper.py --language en --output en.jsonl --format jsonl  # English only
python news_rss_scraper.py --feed setopati                  # Single feed
```

**Sources include:** Kathmandu Post, OnlineKhabar (EN+NE), Setopati, Nagarik News, BBC Nepali, Annapurna Post, Gorkhapatra, Pahilo Post, Khabarhub, Himal Press, AP1 TV, Image Channel, Mero Lagani, plus 7-province OnlineKhabar feeds, Gandak News, Pokhara Hotline, Lumbini Online, Karnali Mission, and more.

## Ekantipur Scraper (8 pages)

Async HTML scraper for ekantipur.com — Nepal's largest Nepali-language news site. RSS returns 404, so this scrapes HTML directly.

```bash
python ekantipur_scraper.py                                 # National + all 7 provinces
python ekantipur_scraper.py --province gandaki              # Single province
python ekantipur_scraper.py --national                      # National only
python ekantipur_scraper.py --output corpus.jsonl --format jsonl
```

## Government Ministry Scraper (17 ministries)

Scrapes press releases, notices, circulars from 17 Nepal federal ministry websites.

```bash
python govt_scraper.py --list                               # List all ministries
python govt_scraper.py --ministry mof                       # Single ministry
python govt_scraper.py --all --output data/govt/            # All ministries
python govt_scraper.py --all --pages 10 --output data/govt/ # Deep scrape
```

**How it works:** Most Nepal govt websites use `/category/press-release/` URL patterns and `/content/{id}/` article links. The scraper handles both category-listing and table-based layouts, Nepali (Bikram Sambat) dates, and SSL certificate issues.

**Adding a new ministry:**
```python
MINISTRIES["new_ministry"] = MinistryConfig(
    source_id="new_ministry",
    name="Ministry of XYZ",
    name_ne="XYZ मन्त्रालय",
    base_url="https://moxyz.gov.np",
    endpoints={"press_release": "/category/press-release/", "notice": "/category/notice/"},
)
```

## DAO Scraper (77 districts)

Scrapes all 77 District Administration Offices — these issue curfews, prohibitory orders, and local emergency notifications.

```bash
python dao_scraper.py --list                                # List all 77 districts by province
python dao_scraper.py --district kathmandu                  # Single district
python dao_scraper.py --province Gandaki                    # All districts in a province
python dao_scraper.py --priority --output data/dao/         # 15 priority districts
python dao_scraper.py --all --output data/dao/              # All 77 (slow!)
python dao_scraper.py --district kaski --category notice-ne # Nepali notices
```

**URL pattern:** `https://dao{district}.moha.gov.np` (e.g., daokathmandu.moha.gov.np)

## Config Files

### `sources/govt_sources_registry.yaml`
Complete registry of 53 government sources organized by type:
- 22 Federal Ministries (OPMCM, MOHA, MOFA, MOF, MOHP, MOD, etc.)
- 6 Constitutional Bodies (Election Commission, CIAA, Auditor General, PSC, NHRC, NWC)
- 5 Regulatory Bodies (Nepal Rastra Bank, SEBON, NTA, NERC, CAAN)
- 2 Judiciary (Supreme Court, Judicial Council)
- 6 Security Services (Nepal Police, CIB, APF, Nepal Army, NID, Immigration)
- 3 Disaster Sources (BIPAD Portal, DRR Portal, DHM)
- 2 Parliament (House of Representatives, National Assembly)
- 7 Provincial Governments
- 6 Metropolitan Cities (Kathmandu, Lalitpur, Bharatpur, Pokhara, Biratnagar, Birgunj)

### `sources/social_sources.yaml`
59 Twitter/X accounts scraped via Nitter (no API needed):
- 7 Government & Official accounts
- 26 Political Leaders (all major party leaders)
- 5 Party Official accounts (RSP, NC, UML, Maoist, RPP)
- 4 News Media accounts
- 14 Journalists & Analysts
- 15 Hashtags (English + Nepali)
- 10 Text search queries

## Output Format

All scrapers output JSON with consistent fields:

```json
{
  "id": "unique_id",
  "title": "Article title (may be in Devanagari)",
  "url": "https://...",
  "source_id": "source_identifier",
  "source_name": "Human-readable source name",
  "language": "en|ne",
  "published_at": "2025-01-15T00:00:00",
  "summary": "Article excerpt if available",
  "scraped_at": "2025-01-15T12:00:00"
}
```

## Building a Corpus

Scrape everything into JSONL for fine-tuning:

```bash
mkdir -p corpus

# Government ministries (17 sources)
python govt_scraper.py --all --pages 10 --output corpus/govt/

# District offices (77 districts — run overnight)
python dao_scraper.py --all --output corpus/dao/

# News RSS (33 feeds)
python news_rss_scraper.py --output corpus/news.jsonl --format jsonl

# Ekantipur (8 pages)
python ekantipur_scraper.py --output corpus/ekantipur.jsonl --format jsonl
```

Daily cron for accumulating articles:
```bash
0 6 * * * cd /path/to/nepali-corpus && python news_rss_scraper.py --output /data/corpus/news_$(date +\%Y\%m\%d).jsonl --format jsonl
0 7 * * * cd /path/to/nepali-corpus && python ekantipur_scraper.py --output /data/corpus/ekantipur_$(date +\%Y\%m\%d).jsonl --format jsonl
0 8 * * 0 cd /path/to/nepali-corpus && python dao_scraper.py --priority --output /data/corpus/dao/
```

## Notes

- **Rate limiting:** All scrapers include configurable delays (0.5-1s). Be respectful.
- **SSL issues:** Nepal govt sites often have expired SSL certs. Govt/DAO scrapers disable verification.
- **Nepali dates:** Government documents use Bikram Sambat (BS) dates (e.g., 2081-09-15), preserved as-is.
- **Deduplication:** All scrapers deduplicate by URL within a run. For cross-run dedup, use `id` or `url`.
- **Nitter scraping:** Social sources require a working Nitter instance. Instances go down frequently — check `sources/social_sources.yaml` for alternatives.

## License

MIT

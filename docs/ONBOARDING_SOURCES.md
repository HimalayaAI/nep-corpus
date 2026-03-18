# Onboarding New Sources to Nepali Corpus

This guide explains how to add new data sources to the HimalayaAI Nepali Corpus pipeline. The system is designed to be highly configurable, with powerful generic miners that minimize the need to write new Python code for standard sites. 

## Source Discovery

The pipeline relies on registries located in the `sources/` directory. All files here are automatically discovered and loaded by the `SourceRegistry`. 

### Supported Formats

The registry supports two formats:
- **YAML (`.yaml`)**: Ideal for smaller, human-curated lists (e.g. `news_rss_registry.yaml`, `govt_sources_registry.yaml`, `social_sources.yaml`).
- **JSONL (`.jsonl`)**: Ideal for large, bulk-exported lists consisting of thousands of domains (e.g. `news_bulk_registry.jsonl`).

## 1. General Websites (News & Blogs)

The `ScrapeCoordinator` uses a powerful `DiscoveryMiner` to automatically discover and extract articles from generic HTML websites. It traverses sitemaps, RSS feeds, category/navigation menus, paginated lists, and date-based archives effortlessly. 

To add a basic source (like a news site or blog), simply append a 3-field minimum configuration to any of the registry files:

```jsonl
{"id": "my_unique_source_id", "name": "My Website Name", "url": "https://mywebsite.com"}
```
> ***Note:** When appending to JSONL files, provide one JSON object per line.*

The system will automatically assign defaults (e.g., `source_type="html"`, `language="ne"`, `priority=3`).

### Advanced Fields (Optional)

If your source requires explicit constraints, provide optional fields to the dictionary:

| Field | Type | Default | Description |
|---|---|---|---|
| `source_type` | string | `"html"` | Usually `rss`, `html`, `government`, `social`. |
| `language` | string | `"ne"` | The primary language (`"ne"`, `"en"`, or `"mixed"`). |
| `category` | string | `None` | E.g. `national`, `provincial`, `business`, `sports`. |
| `tags` | list[str] | `[]` | Any tags for filtering or categorization. |
| `priority` | int | `3` | Priority level 1 (highest) to 5. Extracted first in the queue. |
| `district` | string | `None` | Filter or categorize by district (metadata). |

---

## 2. Government & Regulatory Sources

Government sources often have specific layouts for press releases, circulars, and curfews. These are typically managed in `sources/govt_sources_registry.yaml`.

### Typical Ministries 

Provide explicit endpoints for `ScrapeCoordinator` to paginate and spider:

```yaml
  - id: some_ministry
    name: Ministry of Something
    name_ne: केही मन्त्रालय
    base_url: https://something.gov.np
    scraper_class: ministry_generic
    endpoints:
      press_release_en: /en/page/press-release
      notice_ne: /page/notice
    priority: 2
```

### Regulatory Bodies (Recursive Search)

If a site lacks clear pagination endpoints, use the `regulatory` scraper class which recursively crawls the site for keywords (`notice`, `सूचना`, `press`):

```yaml
  - id: new_regulator
    name: Some Regulatory Board
    base_url: https://board.gov.np
    scraper_class: regulatory
    priority: 2
```

---

## 3. Social Media Sources

Social media sources (Twitter/X) are scraped without an active API using temporary Nitter endpoints. Configure these targets in `sources/social_sources.yaml`.

**Track Accounts:**
```yaml
  - username: KathmanduPost
    name: The Kathmandu Post
```

**Track Hashtags / Topics:**
```yaml
  - tag: NepalElection
    name: Nepal Election
```

---

## 4. Complex Domains (Custom Scrapers)

If a source requires complex JavaScript execution, heavily guarded APIs, or socket listeners (such as live stock market data), you should author a Python script inside `nepali_corpus/core/services/scrapers/`.

1. Create a `custom_scraper.py` class that inherits from `ScraperBase`. 
2. Ensure the class yields dictionaries or `RawRecord` instances.
3. Wire the scraper into `nepali_corpus/core/services/scrapers/control.py` inside the `_build_jobs()` method:

```python
    elif entry.scraper_class == "my_custom_class":
        from .custom_scraper import CustomScraper
        jobs.append(ScrapeJob(
            name=f"custom:{entry.source_id}",
            category="Custom",
            scraper_class="my_custom_class",
            func=lambda e=entry: CustomScraper(e).scrape()
        ))
```

## 5. Validation and Testing

Before executing the full coordinator pipeline, ensure your registry configurations are valid according to the Pydantic schema:

```bash
python scripts/validate_sources.py sources/govt_sources_registry.yaml
```

**Testing the Coordinator with the new category / source:**

```bash
# Provide custom rate-limit and limits to avoid being blocked while testing
python scripts/corpus_cli.py coordinator \
       --categories Gov \
       --govt-groups federal_ministries \
       --max-pages 2 \
       --workers 2 \
       --rate-limit 1.0 
```

Navigate to the **Dashboard (`localhost:8000`)** during your test run to observe the URL queue, extraction rates, and ensure the `BoilerplateDetector` is appropriately filtering out navigation menus and footers.

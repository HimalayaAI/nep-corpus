# Bug Fixes & Stability Improvements

## April 15, 2026

### Pipeline Stability Improvements

- **Resume flow now matches normal run** — Flush/drain/enrichment order is consistent
- **Force-flush enrichment buffer on resume** — Prevents stranded records
- **Mark URLs visited only after DB success** — DB write failures don't corrupt visited state
- **Constrained enrichment workers** — Single worker per enrichment task avoids worker explosion
- **Atomic URL tracking** — Store → Mark → Memory ordering prevents duplicates/misses

## April 17, 2026

### 1. Fixed the 390k Duplicate URL Trap
**The Problem:** During older test runs, the buggy code successfully scraped 390,000 URLs from RSS feeds but failed to extract the text. The URLs were permanently saved into the `visited_urls` database table. When re-running the scraper, it checked the database, saw the 390,000 URLs, and instantly threw them away as duplicates, preventing them from ever being enriched.

**The Fix:** 
- Modified `corpus_cli.py` to naturally default to `--no-skip-successful` mode. 
- Updated `_handle_results` in `control.py` to entirely **bypass the database checking step**. The pipeline now only skips URLs that are already safely stored with text in `training_documents`.

### 2. Fixed the `double free or corruption` Crash
**The Problem:** The heavy-duty production command (`--workers 20 --enrichment-batch-size 100`) was throwing an OS-level memory corruption crash. The pipeline was spawning hundreds of background enrichment tasks simultaneously, overloading Python's underlying SSL/HTTP C-bindings.

**The Fix:** Introduced an `asyncio.Semaphore(2)` on the background scheduling queue. Even if you demand 100 batches at once, the pipeline will now strictly throttle extraction to no more than 2 distinct batches at a time, ensuring memory safety while parsing thousands of pages.

### 3. Streaming `raw.jsonl` Writes
**The Problem:** If you opened `raw.jsonl` while the scraper was actively running, all records said `content: null`. The scraper was appending empty metadata to the file, and only writing the actual text at the very, very end of the run. This is a fatal flaw for a script meant to run for weeks continuously.

**The Fix:** Rearchitected `_process_immediate_enrichment`. We handed the `JsonlWriter` directly to the background extraction threads. Now, as the scraper pulls articles from the internet, it directly writes the fully enriched texts into the `.jsonl` file in real-time.

### 4. Eliminated Silent Enrichment Aborts & Transient Errors
**The Problem:** Previously, if a *single* URL failed a database insertion, or a single thread timed out, the entire batch of 50 documents would be silently discarded. Minor DB errors were stopping pipelines.

**The Fix:** Overhauled the error handling logic to be **fail-soft**. Records are now processed iteratively, and `control.py` will catch and log individual timeouts rather than dumping the whole batch. Database operations now safely ignore single-record errors (like unique constraint violations) and continue processing.

### 5. Prevented Counter State Overwrites
**The Problem:** The `docs_saved` state was being overwritten (`=`) instead of accumulated (`+=`), causing the final run summaries to report inaccurate numbers.

**The Fix:** The metrics tracking variables were successfully patched to be additive counters, providing perfectly accurate summary logs.

### 6. Fixed Async Task Discovery Races
**The Problem:** Async discovery jobs on large domains were sometimes left hanging or were not cleanly awaited before the scraper closed its JSON streams, leading to partial data loss of freshly discovered URLs.

**The Fix:** Added an explicit `_discovery_futures` array tracker and bounding lock to ensure all async background discovery tasks are cleanly awaited and fully extracted before gracefully exiting the run loop.

### 7. Missing PDF Parsing Flags
**The Problem:** PDF text extraction features were incorrectly disabled in the post-run phase because the boolean tags weren't systematically propagated from the CLI.

**The Fix:** The flag variables (`ocr_enabled`, `pdf_enabled`) are now correctly handed down the function chain ensuring PDF document content isn't dropped during enrichment.

### 8. Code Cleanup
**The Action:** Stripped out dozens of noisy boilerplate block comments (e.g., `# --- Gov Category ---`) and messy debug traces recently added to `control.py` to ensure the codebase remains clean and professional.

## April 22, 2026

### 1. Unbounded Memory Leaks via Background Queues
**The Problem:** The pipeline was storing all PDF URLs in an infinitely growing list and scheduling `asyncio.create_task` for enrichment without bounding the pending tasks. For long-running continuous scrapes, this would eventually exceed container memory limits and trigger OOM (Out Of Memory) kills.
**The Fix:** Implemented a periodic `_enrichment_flush_task` that forcefully flushes and drains the memory buffer every 30 seconds if records are older than 60 seconds, guaranteeing unbounded memory accumulation is stopped.

### 2. Database Connection Hangs & Silent Failures
**The Problem:** Long-running database operations and transient connection drops were causing pipelines to hang indefinitely or silently skip batches without retrying.
**The Fix:** 
- Hardened `asyncpg` pool `acquire()` calls with a strict 15.0-second timeout.
- Introduced robust `_retry_db_operation` wrappers using exponential backoff to handle transient 5xx HTTP drops and database disconnects gracefully.
- Re-architected connection pooling to dynamically size based on the number of workers (`min = workers * 0.5`, `max = workers * 2.5`).

### 3. Missing Empty-Key Deduplication Bug
**The Problem:** If raw documents were scraped without a valid `dedup_key` (null or empty string), the deduplication code falsely flagged them as duplicates of each other and dropped them.
**The Fix:** Updated `deduplicate` to explicitly ignore deduplication keys that are empty or `None`, falling back purely to URL-based deduplication for those records.

### 4. Oversized Payload Memory Spikes
**The Problem:** The enrichment fetcher was vulnerable to downloading infinitely large payloads (like huge corrupted PDFs or continuous streams), immediately crashing the instance memory.
**The Fix:** Enforced a strict 50MB `MAX_SIZE` limit by streaming chunked content. If a file exceeds the threshold, the download is forcefully truncated and aborted early.

### 5. Blind Network Fetching Vulnerabilities
**The Problem:** Network requests for embedded PDFs and images had `verify=False`, exposing the pipeline to potential Man-in-the-Middle (MITM) attacks and routing manipulation.
**The Fix:** Enforced strict TLS verification (`verify=True`) across all Python `requests.get` extraction layers.

### 6. Dynamic SQL Injection Hardening
**The Problem:** The `update_pipeline_run` and `update_pipeline_job` endpoints relied on basic string checks for database column updates.
**The Fix:** Locked down all dynamic SQL query generation by asserting column names against strict whitelist `frozenset` objects and verifying them via strict identifier regex guards.

### 7. Massive Rust Performance Uplift (v0.2.0)
**The Problem:** Native Python string operations, looping, and regex matching were severely bottlenecking the pipeline's overall HTML extraction speed.
**The Fix:** 
- Upgraded the `rust_url_dedup` engine to v0.2.0, introducing Rayon-powered multithreading and `aho-corasick` automata for massive batch operations.
- Replaced sequential Python text cleaning, HTML boilerplate stripping, NFC normalization, and `devanagari_ratio` counting with high-performance $O(N)$ native Rust FFI calls.
- Upgraded the core hashing algorithm from MD5 to BLAKE3 for drastically faster deduplication key generation.
- Empty content checks now execute inside the DB storage layer directly, bypassing full object instantiations to save Python overhead.

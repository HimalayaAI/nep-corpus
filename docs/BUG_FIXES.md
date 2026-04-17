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

### 4. No More Ctrl+C Data Loss
**The Problem:** Pressing Ctrl+C to gracefully stop the scraper would unintentionally skip the final database/file saving block, erasing whatever the scraper was currently working on.

**The Fix:** Overrode the `is_set()` blocking flag. Even if you send a kill signal, the script will patiently pause, wait for the background queue to finish extracting text, safely save the final `raw.jsonl` lines, and then cleanly shut down.

### 5. Eliminated Silent Enrichment Aborts & Transient Errors
**The Problem:** Previously, if a *single* URL failed a database insertion, or a single thread timed out, the entire batch of 50 documents would be silently discarded. Minor DB errors were stopping pipelines.

**The Fix:** Overhauled the error handling logic to be **fail-soft**. Records are now processed iteratively, and `control.py` will catch and log individual timeouts rather than dumping the whole batch. Database operations now safely ignore single-record errors (like unique constraint violations) and continue processing.

### 6. Prevented Counter State Overwrites
**The Problem:** The `docs_saved` state was being overwritten (`=`) instead of accumulated (`+=`), causing the final run summaries to report inaccurate numbers.

**The Fix:** The metrics tracking variables were successfully patched to be additive counters, providing perfectly accurate summary logs.

### 7. Fixed Async Task Discovery Races
**The Problem:** Async discovery jobs on large domains were sometimes left hanging or were not cleanly awaited before the scraper closed its JSON streams, leading to partial data loss of freshly discovered URLs.

**The Fix:** Added an explicit `_discovery_futures` array tracker and bounding lock to ensure all async background discovery tasks are cleanly awaited and fully extracted before gracefully exiting the run loop.

### 8. Missing PDF Parsing Flags
**The Problem:** PDF text extraction features were incorrectly disabled in the post-run phase because the boolean tags weren't systematically propagated from the CLI.

**The Fix:** The flag variables (`ocr_enabled`, `pdf_enabled`) are now correctly handed down the function chain ensuring PDF document content isn't dropped during enrichment.

### 9. Code Cleanup
**The Action:** Stripped out dozens of noisy boilerplate block comments (e.g., `# --- Gov Category ---`) and messy debug traces recently added to `control.py` to ensure the codebase remains clean and professional.

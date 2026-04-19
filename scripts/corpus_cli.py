#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
import logging
from datetime import datetime

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nepali_corpus.pipeline.runner import (
    ingest_sources,
    ingest_sources_iter,
    save_raw_jsonl,
    load_raw_jsonl,
    enrich_records,
    normalize_and_filter,
    save_normalized_jsonl,
    load_normalized_jsonl,
    to_training_docs,
)
from nepali_corpus.core.utils.dedup import deduplicate
from nepali_corpus.core.utils.export import export_jsonl
from nepali_corpus.core.utils.io import maybe_gzip_path
from nepali_corpus.core.utils.writer import JsonlWriter


def cmd_ingest(args: argparse.Namespace) -> None:
    output = maybe_gzip_path(args.output, args.gzip)
    
    import asyncio
    from nepali_corpus.core.services.storage.env_storage import EnvStorageService

    async def _run_ingest():
        storage = EnvStorageService()
        await storage.initialize()
        session = storage.create_session()
        
        writer = JsonlWriter(output, gzip_output=args.gzip)
        print(f"Starting incremental ingest to {output} and DB...")
        try:
            for rec in ingest_sources_iter(
                sources=args.sources,
                govt_registry_path=args.govt_registry,
                govt_registry_groups=args.govt_groups,
                govt_pages=args.govt_pages,
            ):
                writer.write(rec)
                # Incremental DB Sync
                try:
                    await session.store_raw_records([rec])
                except Exception as e:
                    print(f"Incremental sync failed for {rec.url}: {e}")
            writer.flush()
        finally:
            writer.close()
            await storage.close()
        print(f"Saved {writer.count} raw records to {output}")

    try:
        asyncio.run(_run_ingest())
    except Exception as e:
        print(f"Ingest failed: {e}")


def cmd_enrich(args: argparse.Namespace) -> None:
    records = load_raw_jsonl(args.input)
    enriched = enrich_records(records, cache_dir=args.cache_dir)
    # update content with extracted text when available
    updated = []
    for rec, extracted in enriched:
        if extracted:
            rec.content = extracted
        updated.append(rec)
    output = maybe_gzip_path(args.output, args.gzip)
    count = save_raw_jsonl(updated, output, gzip_output=args.gzip)
    print(f"Saved {count} enriched records to {args.output}")


def cmd_clean(args: argparse.Namespace) -> None:
    records = load_raw_jsonl(args.input)
    enriched = [(r, r.content) for r in records]
    docs = normalize_and_filter(enriched, min_chars=args.min_chars, nepali_ratio=args.nepali_ratio)
    output = maybe_gzip_path(args.output, args.gzip)
    count = save_normalized_jsonl(docs, output, gzip_output=args.gzip)
    print(f"Saved {count} cleaned documents to {args.output}")


def cmd_dedup(args: argparse.Namespace) -> None:
    docs = load_normalized_jsonl(args.input)
    unique = deduplicate(docs)
    output = maybe_gzip_path(args.output, args.gzip)
    count = save_normalized_jsonl(unique, output, gzip_output=args.gzip)
    print(f"Saved {count} deduplicated documents to {args.output}")


def cmd_export(args: argparse.Namespace) -> None:
    docs = load_normalized_jsonl(args.input)
    training = to_training_docs(docs)
    output = maybe_gzip_path(args.output, args.gzip)
    count = export_jsonl(training, output, gzip_output=args.gzip)
    print(f"Exported {count} training documents to {args.output}")


def cmd_all(args: argparse.Namespace) -> None:
    import datetime
    
    prefix = "corpus"
    if getattr(args, "govt_groups", None):
        # use the first group as a prefix
        prefix = "_".join(args.govt_groups[:2])
    elif getattr(args, "sources", None):
        prefix = "_".join(args.sources[:2])
        
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{prefix}_{ts}"
    
    if args.raw_out == "data/raw/raw.jsonl":
        args.raw_out = f"data/raw/{name}.jsonl"
    if args.enriched_out == "data/enriched/enriched.jsonl":
        args.enriched_out = f"data/enriched/{name}.jsonl"
    if args.cleaned_out == "data/enriched/cleaned.jsonl":
        args.cleaned_out = f"data/cleaned/{name}.jsonl"
    if args.dedup_out == "data/enriched/dedup.jsonl":
        args.dedup_out = f"data/dedup/{name}.jsonl"
    if args.final_out == "data/final/training.jsonl":
        args.final_out = f"data/final/{name}.jsonl"

    raw_out = args.raw_out
    enriched_out = args.enriched_out
    cleaned_out = args.cleaned_out
    dedup_out = args.dedup_out
    final_out = args.final_out

    raw_out = maybe_gzip_path(raw_out, args.gzip)
    enriched_out = maybe_gzip_path(enriched_out, args.gzip)
    cleaned_out = maybe_gzip_path(cleaned_out, args.gzip)
    dedup_out = maybe_gzip_path(dedup_out, args.gzip)
    final_out = maybe_gzip_path(final_out, args.gzip)

    # Sync to DB and Pipeline logic
    import asyncio
    from nepali_corpus.core.services.storage.env_storage import EnvStorageService

    async def _run_pipeline():
        storage = EnvStorageService()
        await storage.initialize()
        session = storage.create_session()
        
        # 1. SCRAPE (Incremental)
        all_records = []
        writer = JsonlWriter(raw_out, gzip_output=args.gzip)
        print(f"Starting incremental scrape to {raw_out} and DB...")
        try:
            for rec in ingest_sources_iter(
                sources=args.sources,
                govt_registry_path=args.govt_registry,
                govt_registry_groups=args.govt_groups,
                govt_pages=args.govt_pages,
            ):
                writer.write(rec)
                all_records.append(rec)
                # Incremental DB Sync
                try:
                    await session.store_raw_records([rec])
                except Exception as e:
                    print(f"Incremental sync failed for {rec.url}: {e}")
            writer.flush()
        finally:
            writer.close()
            
        print(f"Scrapped {len(all_records)} raw records.")

        # 2. ENRICH (Batch)
        print("Enriching records...")
        enriched = enrich_records(all_records, cache_dir=args.cache_dir)
        updated = []
        for rec, extracted in enriched:
            if extracted:
                rec.content = extracted
            updated.append(rec)
        save_raw_jsonl(updated, enriched_out, gzip_output=args.gzip)

        # 3. CLEAN (Batch)
        print("Cleaning and normalizing...")
        docs = normalize_and_filter([(r, r.content) for r in updated], min_chars=args.min_chars, nepali_ratio=args.nepali_ratio)
        save_normalized_jsonl(docs, cleaned_out, gzip_output=args.gzip)

        # 4. DEDUP (Batch)
        print("Deduplicating...")
        unique = deduplicate(docs)
        save_normalized_jsonl(unique, dedup_out, gzip_output=args.gzip)

        # 5. EXPORT & FINAL SYNC
        print("Exporting training data...")
        training = to_training_docs(unique)
        export_jsonl(training, final_out, gzip_output=args.gzip)
        
        print(f"Syncing {len(training)} training documents to database...")
        await session.store_training_documents(training)
        
        await storage.close()
        print("Pipeline complete")

    try:
        asyncio.run(_run_pipeline())
    except Exception as e:
        print(f"Pipeline failed: {e}")

    print("Pipeline complete")


def cmd_coordinator(args: argparse.Namespace) -> None:
    """Run the coordinator directly — parallel scrape with run tracking."""
    from nepali_corpus.core.services.storage.env_storage import EnvStorageService
    from nepali_corpus.core.services.scrapers.control import ScrapeCoordinator

    run_id = args.resume or args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = f"data/runs/{run_id}"
    os.makedirs(output_dir, exist_ok=True)

    # Write meta.json
    meta = {
        "run_id": run_id,
        "sources": args.sources,
        "categories": args.categories,
        "workers": args.workers,
        "max_pages": args.max_pages,
        "gzip": args.gzip,
        "started_at": datetime.now().isoformat(),
        "resumed": bool(args.resume),
    }
    meta_path = os.path.join(output_dir, "meta.json")
    if not os.path.exists(meta_path):
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    output_path = os.path.join(output_dir, "raw.jsonl")
    if args.gzip:
        output_path += ".gz"

    print(f"{'Resuming' if args.resume else 'Starting'} coordinator run: {run_id}")
    print(f"Output dir: {output_dir}")
    print(f"Workers: {args.workers}, Max pages: {args.max_pages}")
    print(f"Categories: {args.categories or ['Gov', 'News']}")
    print()

    log_file = os.path.join(output_dir, "run.log")

    async def _run():
        storage = EnvStorageService()
        await storage.initialize()
        coordinator = ScrapeCoordinator(
            storage,
            enrichment_batch_size=args.enrichment_batch_size,
            rate_limit=args.rate_limit,
            max_concurrent=args.max_concurrent,
            source_timeout=args.source_timeout,
            checkpoint_interval=args.checkpoint_interval,
            enrichment_workers=args.enrichment_workers,
            skip_successful_only=args.skip_successful,
            ocr_enabled=getattr(args, "ocr", False),
            pdf_enabled=getattr(args, "pdf", False),
        )

        # Signal handling for graceful shutdown
        loop = asyncio.get_running_loop()

        def _on_signal():
            print("\n⚠️  Shutdown signal received — finishing in-flight jobs...")
            coordinator.request_shutdown()

        for sig in (signal.SIGTERM,):
            loop.add_signal_handler(sig, _on_signal)

        loop.add_signal_handler(signal.SIGINT, lambda: print("\n Ctrl+C is disabled for safety! Background scraping will continue. To actually kill, use 'Ctrl+\\' or 'kill <pid>'."))

        try:
            if args.resume:
                await coordinator.resume_run(
                    run_id=args.resume,
                    workers=args.workers,
                    max_pages=args.max_pages,
                    gzip_output=args.gzip,
                    output_path=output_path,
                    govt_registry_path=args.govt_registry,
                    govt_registry_groups=args.govt_groups,
                    output_dir=output_dir,
                    log_file=log_file,
                    num_sources=args.num_sources,
                )
            else:
                await coordinator.start(
                    workers=args.workers,
                    max_pages=args.max_pages,
                    categories=args.categories,
                    gzip_output=args.gzip,
                    output_path=output_path,
                    govt_registry_path=args.govt_registry,
                    govt_registry_groups=args.govt_groups,
                    run_id=run_id,
                    output_dir=output_dir,
                    log_file=log_file,
                    num_sources=args.num_sources,
                )

            # Wait for the coordinator task to finish
            while coordinator.is_running():
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            print("\n  KeyboardInterrupt — flushing...")
            coordinator.request_shutdown()
            await asyncio.sleep(1)
        finally:
            # Write checkpoint
            coordinator.write_checkpoint(output_dir)

            # Print summary
            state_dict = coordinator.state.to_dict()
            print()
            print("=" * 60)
            print(f"Run {run_id} {'interrupted' if coordinator._shutdown_event.is_set() else 'completed'}")
            print(f"  URLs crawled : {state_dict['urls_crawled']}")
            print(f"  Docs saved   : {state_dict['docs_saved']}")
            print(f"  Failures     : {state_dict['urls_failed']}")
            print(f"  Elapsed      : {state_dict['elapsed']}")
            print(f"  Checkpoint   : {output_dir}/checkpoint.json")
            print("=" * 60)

            if coordinator._shutdown_event.is_set():
                print(f"\nTo resume: python scripts/corpus_cli.py coordinator --resume {run_id}")

            await storage.close()

    asyncio.run(_run())


def cmd_rerun_failed(args: argparse.Namespace) -> None:
    """Rerun extraction for NULL records by pulling directly from DB."""
    from nepali_corpus.core.services.storage.env_storage import EnvStorageService
    from nepali_corpus.core.services.scrapers.control import ScrapeCoordinator

    async def _run():
        storage = EnvStorageService()
        await storage.initialize()
        coordinator = ScrapeCoordinator(
            storage,
            enrichment_workers=args.enrichment_workers,
            enrichment_batch_size=args.batch_size,
            rate_limit=args.rate_limit,
            max_concurrent=args.max_concurrent,
            ocr_enabled=getattr(args, "ocr", False),
            pdf_enabled=getattr(args, "pdf", False),
        )

        print(f"⚙️  Starting Rerun-Failed mode for NULL records...")
        print(f"Workers: {args.enrichment_workers}, Batch Size: {args.batch_size}")
        if args.limit:
            print(f"Limit: {args.limit} records")

        await coordinator.run_rerun_failed(
            batch_size=args.batch_size,
            limit=args.limit,
            log_file=args.log_file
        )

    asyncio.run(_run())


def cmd_seed_hf_urls(args: argparse.Namespace) -> None:
    """Seed visited_urls from an HF dataset URL column."""
    import hashlib
    import json as _json
    from datasets import load_dataset
    from nepali_corpus.core.services.storage.env_storage import EnvStorageService

    async def _run():
        storage = EnvStorageService()
        await storage.initialize()

        cache_path = args.cache_path
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)

        def _url_hash(url: str) -> str:
            return hashlib.md5(url.encode("utf-8")).hexdigest()

        async def _seed_from_iter(url_iter, write_cache: bool) -> int:
            total = 0
            buffer = []
            cache_f = open(cache_path, "w", encoding="utf-8") if write_cache else None
            try:
                for url in url_iter:
                    if not url:
                        continue
                    url = str(url).strip()
                    if not url:
                        continue
                    buffer.append((_url_hash(url), url))
                    if write_cache:
                        cache_f.write(_json.dumps({"url": url}, ensure_ascii=False) + "\n")
                    if len(buffer) >= args.batch_size:
                        await storage._db.executemany(
                            "INSERT INTO visited_urls (url_hash, url) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                            buffer,
                        )
                        total += len(buffer)
                        buffer = []
                if buffer:
                    await storage._db.executemany(
                        "INSERT INTO visited_urls (url_hash, url) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                        buffer,
                    )
                    total += len(buffer)
            finally:
                if cache_f:
                    cache_f.close()
            return total

        if args.refresh_cache:
            dataset = load_dataset(
                args.repo_id,
                name=args.config_name,
                split=args.split,
                streaming=True,
            )
            total = await _seed_from_iter((row.get("url") for row in dataset), write_cache=True)
        else:
            if not os.path.exists(cache_path):
                raise FileNotFoundError(f"Cache not found: {cache_path}")
            with open(cache_path, "r", encoding="utf-8") as f:
                urls = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = _json.loads(line)
                        urls.append(obj.get("url"))
                    except Exception:
                        urls.append(line)
            total = await _seed_from_iter(urls, write_cache=False)

        print(f"Seeded {total} URLs into visited_urls")
        await storage.close()

    asyncio.run(_run())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nepali corpus pipeline CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Run scrapers and write raw JSONL")
    p_ingest.add_argument("--output", "-o", required=True)
    p_ingest.add_argument(
        "--sources",
        help="Comma-separated source types (rss, ekantipur, govt, dao). govt includes DAO by default unless govt groups are specified.",
    )
    p_ingest.add_argument("--sources-registry", dest="govt_registry", help="Path to sources/govt_sources_registry.yaml")
    p_ingest.add_argument("--govt-registry", help="Path to sources/govt_sources_registry.yaml")
    p_ingest.add_argument(
        "--sources-groups",
        dest="govt_groups",
        help="Comma-separated groups from registry (e.g. federal_ministries,constitutional_bodies)",
    )
    p_ingest.add_argument(
        "--govt-groups",
        help="Comma-separated groups from registry (e.g. federal_ministries,constitutional_bodies)",
    )
    p_ingest.add_argument(
        "--sources-pages",
        dest="govt_pages",
        type=int,
        default=3,
        help="Max pages per govt endpoint (default: 3)",
    )
    p_ingest.add_argument(
        "--govt-pages",
        type=int,
        default=3,
        help="Max pages per govt endpoint (default: 3)",
    )
    p_ingest.add_argument("--gzip", action="store_true", help="Write .jsonl.gz output")
    p_ingest.set_defaults(func=cmd_ingest)

    p_enrich = sub.add_parser("enrich", help="Fetch full text and write enriched JSONL")
    p_enrich.add_argument("--input", "-i", required=True)
    p_enrich.add_argument("--output", "-o", required=True)
    p_enrich.add_argument("--cache-dir", default="data/html_cache")
    p_enrich.add_argument("--gzip", action="store_true", help="Write .jsonl.gz output")
    p_enrich.set_defaults(func=cmd_enrich)

    p_clean = sub.add_parser("clean", help="Normalize, filter Nepali, and write cleaned JSONL")
    p_clean.add_argument("--input", "-i", required=True)
    p_clean.add_argument("--output", "-o", required=True)
    p_clean.add_argument("--min-chars", type=int, default=200)
    p_clean.add_argument("--nepali-ratio", type=float, default=0.4)
    p_clean.add_argument("--gzip", action="store_true", help="Write .jsonl.gz output")
    p_clean.set_defaults(func=cmd_clean)

    p_dedup = sub.add_parser("dedup", help="Deduplicate cleaned JSONL")
    p_dedup.add_argument("--input", "-i", required=True)
    p_dedup.add_argument("--output", "-o", required=True)
    p_dedup.add_argument("--gzip", action="store_true", help="Write .jsonl.gz output")
    p_dedup.set_defaults(func=cmd_dedup)

    p_export = sub.add_parser("export", help="Export training JSONL")
    p_export.add_argument("--input", "-i", required=True)
    p_export.add_argument("--output", "-o", required=True)
    p_export.add_argument("--gzip", action="store_true", help="Write .jsonl.gz output")
    p_export.set_defaults(func=cmd_export)

    p_all = sub.add_parser("all", help="Run full pipeline")
    p_all.add_argument(
        "--sources",
        help="Comma-separated source types (rss, ekantipur, govt, dao). govt includes DAO by default unless govt groups are specified.",
    )
    p_all.add_argument("--sources-registry", dest="govt_registry", help="Path to sources/govt_sources_registry.yaml")
    p_all.add_argument("--govt-registry", help="Path to sources/govt_sources_registry.yaml")
    p_all.add_argument(
        "--sources-groups",
        dest="govt_groups",
        help="Comma-separated groups from registry (e.g. federal_ministries,constitutional_bodies)",
    )
    p_all.add_argument(
        "--govt-groups",
        help="Comma-separated groups from registry (e.g. federal_ministries,constitutional_bodies)",
    )
    p_all.add_argument(
        "--sources-pages",
        dest="govt_pages",
        type=int,
        default=3,
        help="Max pages per govt endpoint (default: 3)",
    )
    p_all.add_argument(
        "--govt-pages",
        type=int,
        default=3,
        help="Max pages per govt endpoint (default: 3)",
    )
    p_all.add_argument("--gzip", action="store_true", help="Write .jsonl.gz output")
    p_all.add_argument("--cache-dir", default="data/html_cache")
    p_all.add_argument("--min-chars", type=int, default=200)
    p_all.add_argument("--nepali-ratio", type=float, default=0.4)
    p_all.add_argument("--raw-out", default="data/raw/raw.jsonl")
    p_all.add_argument("--enriched-out", default="data/enriched/enriched.jsonl")
    p_all.add_argument("--cleaned-out", default="data/enriched/cleaned.jsonl")
    p_all.add_argument("--dedup-out", default="data/enriched/dedup.jsonl")
    p_all.add_argument("--final-out", default="data/final/training.jsonl")
    p_all.set_defaults(func=cmd_all)

    # --- New coordinator subcommand ---
    p_coord = sub.add_parser(
        "coordinator",
        help="Run parallel coordinator with run tracking (recommended for production)",
    )
    p_coord.add_argument(
        "--sources",
        help="Comma-separated source types (rss, ekantipur, govt, dao, social)",
    )
    p_coord.add_argument(
        "--categories",
        help="Comma-separated categories (Gov, News, Social). Default: Gov,News",
    )
    p_coord.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    p_coord.add_argument("--max-pages", type=int, default=3, help="Max pages per source (default: 3)")
    p_coord.add_argument("--govt-registry", help="Path to sources/govt_sources_registry.yaml")
    p_coord.add_argument(
        "--govt-groups",
        help="Comma-separated groups from registry",
    )
    p_coord.add_argument("--gzip", action="store_true", help="Compress output")
    p_coord.add_argument("--num-sources", type=int, help="Limit number of sources to scrape")
    p_coord.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="Resume an interrupted run by its run_id",
    )
    p_coord.add_argument(
        "--run-id",
        help="Custom run ID for this session (defaults to timestamp)",
    )
    # Production tuning flags
    p_coord.add_argument("--rate-limit", type=float, default=2.0,
                         dest="rate_limit",
                         help="Max requests/sec per domain (default: 2.0)")
    p_coord.add_argument("--max-concurrent", type=int, default=20,
                         dest="max_concurrent",
                         help="Max total concurrent requests (default: 20)")
    p_coord.add_argument("--source-timeout", type=int, default=300,
                         dest="source_timeout",
                         help="Per-source timeout in seconds (default: 300)")
    p_coord.add_argument("--enrichment-batch-size", type=int, default=50,
                         dest="enrichment_batch_size",
                         help="Records to buffer before triggering enrichment (default: 50)")
    p_coord.add_argument("--checkpoint-interval", type=int, default=300,
                         dest="checkpoint_interval",
                         help="Seconds between periodic checkpoints (default: 300)")
    p_coord.add_argument("--enrichment-workers", type=int, default=10,
                         dest="enrichment_workers",
                         help="Parallel workers for content extraction (default: 10)")
    p_coord.add_argument("--no-skip-successful", action="store_false",
                         dest="skip_successful", default=True,
                         help="Skip ALL previously visited URLs, not just successful ones")
    p_coord.add_argument("--ocr", action="store_true", default=False,
                         help="Enable image OCR (slow, default: False)")
    p_coord.add_argument("--pdf", action="store_true", default=False,
                         help="Enable embedded PDF extraction (default: False)")
    p_coord.set_defaults(func=cmd_coordinator)

    # --- Rerun-failed subcommand ---
    p_rerun = sub.add_parser(
        "rerun-failed",
        help="Retry extraction for NULL records (Discovery-Free mode)",
    )
    p_rerun.add_argument("--workers", type=int, default=10,
                          dest="enrichment_workers",
                          help="Parallel workers for extraction (default: 10)")
    p_rerun.add_argument("--batch-size", type=int, default=50,
                          dest="batch_size",
                          help="Batch size for DB updates (default: 50)")
    p_rerun.add_argument("--rate-limit", type=float, default=2.0,
                          dest="rate_limit",
                          help="Max requests/sec per domain (default: 2.0)")
    p_rerun.add_argument("--max-concurrent", type=int, default=20,
                          dest="max_concurrent",
                          help="Max total concurrent requests (default: 20)")
    p_rerun.add_argument("--limit", type=int, default=0,
                          help="Limit records to process (default: 0, no limit)")
    p_rerun.add_argument("--log-file", default="data/rerun_failed.log",
                          dest="log_file",
                          help="Log file for repair run (default: data/rerun_failed.log)")
    p_rerun.add_argument("--ocr", action="store_true", default=False,
                          help="Enable image OCR (slow, default: False)")
    p_rerun.add_argument("--pdf", action="store_true", default=False,
                          help="Enable embedded PDF extraction (default: False)")
    p_rerun.set_defaults(func=cmd_rerun_failed)

    # --- Seed HF URLs into visited_urls ---
    p_seed = sub.add_parser(
        "seed-hf-urls",
        help="Seed visited_urls from a Hugging Face dataset URL column",
    )
    p_seed.add_argument("--repo-id", required=True,
                        help="HF dataset repo id (e.g. org/dataset)")
    p_seed.add_argument("--split", default="train",
                        help="HF split to read (default: train)")
    p_seed.add_argument("--config-name", default="default",
                        help="HF config name (default: default)")
    p_seed.add_argument("--batch-size", type=int, default=5000,
                        help="Insert batch size (default: 5000)")
    p_seed.add_argument("--cache-path", default="data/hf_url_cache.jsonl",
                        help="URL cache path (default: data/hf_url_cache.jsonl)")
    p_seed.add_argument("--refresh-cache", action="store_true",
                        help="Refresh cache from HF (default: true)")
    p_seed.add_argument("--no-refresh-cache", action="store_false",
                        dest="refresh_cache",
                        help="Use local cache only")
    p_seed.set_defaults(refresh_cache=True)
    p_seed.set_defaults(func=cmd_seed_hf_urls)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "gzip", False):
        args.gzip = os.getenv("GZIP_OUTPUT", "false").lower() == "true"
    sources_arg = getattr(args, "sources", None)
    if sources_arg:
        args.sources = [s.strip() for s in sources_arg.split(",") if s.strip()]
    govt_groups = getattr(args, "govt_groups", None)
    if govt_groups:
        args.govt_groups = [g.strip() for g in govt_groups.split(",") if g.strip()]
    # Parse categories for coordinator subcommand
    categories_arg = getattr(args, "categories", None)
    if categories_arg and isinstance(categories_arg, str):
        args.categories = [c.strip() for c in categories_arg.split(",") if c.strip()]
    elif categories_arg is None and hasattr(args, "categories"):
        args.categories = None
    if getattr(args, "sources", None) is None and (
        getattr(args, "govt_groups", None) or getattr(args, "govt_registry", None)
    ):
        args.sources = ["govt"]
    if getattr(args, "govt_groups", None) and not getattr(args, "govt_registry", None):
        default_registry = os.path.join("sources", "govt_sources_registry.yaml")
        if os.path.exists(default_registry):
            args.govt_registry = default_registry
    args.func(args)


if __name__ == "__main__":
    main()

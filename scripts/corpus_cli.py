#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys

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

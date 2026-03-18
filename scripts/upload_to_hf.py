#!/usr/bin/env python3
"""
Upload Nepali Corpus to Hugging Face Hub.

Streams data from the local database directly to a Hugging Face dataset repository
in chunks to avoid OOM for large tables. Filters exactly to:
id, text, url, language
"""

import argparse
import asyncio
import logging
import math
import os
import re
import sys
from pathlib import Path

# Add project root to sys.path
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

import json
from datasets import Dataset, load_dataset
from huggingface_hub import HfApi, login, get_token
from nepali_corpus.core.services.storage.env_storage import EnvStorageService

async def get_total_count(session) -> int:
    """Get total number of training documents."""
    query = "SELECT COUNT(*) FROM training_documents"
    val = await session.service._db.fetch_value(query)
    return val or 0


def _parse_url_cache_line(line: str) -> str:
    line = line.strip()
    if not line:
        return ""
    if line.startswith("{"):
        try:
            obj = json.loads(line)
            return obj.get("url", "").strip()
        except Exception:
            return ""
    return line


def load_url_cache(cache_path: str) -> set[str]:
    urls: set[str] = set()
    if not os.path.exists(cache_path):
        return urls
    with open(cache_path, "r", encoding="utf-8") as f:
        for line in f:
            url = _parse_url_cache_line(line)
            if url:
                urls.add(url)
    return urls


def write_url_cache(cache_path: str, urls: set[str]) -> None:
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        for url in sorted(urls):
            f.write(json.dumps({"url": url}, ensure_ascii=False) + "\n")


def refresh_url_cache_from_hf(
    repo_id: str,
    cache_path: str,
    split: str = "train",
    config_name: str = "default",
) -> set[str]:
    logger.info("Refreshing HF URL cache from %s...", repo_id)
    urls: set[str] = set()
    dataset = load_dataset(repo_id, name=config_name, split=split, streaming=True)
    for row in dataset:
        url = row.get("url")
        if url:
            urls.add(url)
    write_url_cache(cache_path, urls)
    logger.info("Cached %d URLs to %s", len(urls), cache_path)
    return urls


def get_max_shard_index(api: HfApi, repo_id: str) -> int:
    """Return the max shard index from existing parquet files in the repo."""
    try:
        files = api.list_repo_files(repo_id, repo_type="dataset")
    except Exception:
        return 0
    pattern = re.compile(r"^data/train-(\d+)-of-(\d+)\.parquet$")
    max_idx = 0
    for path in files:
        m = pattern.match(path)
        if not m:
            continue
        idx = int(m.group(1))
        if idx > max_idx:
            max_idx = idx
    return max_idx


def append_url_cache(cache_path: str, urls: list[str]) -> None:
    if not urls:
        return
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "a", encoding="utf-8") as f:
        for url in urls:
            f.write(json.dumps({"url": url}, ensure_ascii=False) + "\n")


async def stream_training_documents(session, columns: str, batch_size: int):
    # training_documents.id is TEXT, so keep the cursor as string
    last_id = ""
    while True:
        query = f"""
            SELECT {columns}
            FROM training_documents
            WHERE id > $1
            ORDER BY id
            LIMIT $2
        """
        rows = await session.service._db.fetch(query, last_id, batch_size)
        if not rows:
            break
        last_id = str(rows[-1]["id"])
        yield rows


async def count_new_rows(
    session,
    hf_url_set: set[str],
    batch_size: int,
    max_batches: int | None,
) -> int:
    new_count = 0
    max_rows = None
    if max_batches is not None:
        max_rows = max_batches * batch_size

    async for rows in stream_training_documents(session, "id, url", batch_size):
        for row in rows:
            url = row.get("url")
            if not url:
                continue
            if url in hf_url_set:
                continue
            new_count += 1
            if max_rows is not None and new_count >= max_rows:
                return new_count

    return new_count


async def upload_incremental_batches(
    *,
    session,
    api: HfApi,
    repo_id: str,
    token: str,
    batch_size: int,
    hf_url_set: set[str],
    cache_path: str,
    start_index: int,
    total_batches: int,
    max_batches: int | None,
):
    os.makedirs("data/hf_export", exist_ok=True)

    batch_index = start_index
    uploaded_batches = 0
    target_batches = max_batches if max_batches is not None else math.inf
    current_rows: list = []
    current_urls: set[str] = set()

    async for rows in stream_training_documents(session, "id, text, url, language", batch_size):
        for row in rows:
            url = row.get("url")
            if not url:
                continue
            if url in hf_url_set or url in current_urls:
                continue

            current_rows.append(row)
            current_urls.add(url)

            if len(current_rows) >= batch_size:
                logger.info(
                    "Uploading incremental shard %s/%s (%s rows)...",
                    batch_index,
                    total_batches,
                    len(current_rows),
                )
                await _upload_rows_as_parquet(
                    api=api,
                    repo_id=repo_id,
                    token=token,
                    rows=current_rows,
                    shard_index=batch_index,
                    total_batches=total_batches,
                )

                hf_url_set.update(current_urls)
                append_url_cache(cache_path, list(current_urls))

                current_rows = []
                current_urls = set()
                batch_index += 1
                uploaded_batches += 1

                if uploaded_batches >= target_batches:
                    return

    if current_rows and uploaded_batches < target_batches:
        logger.info(
            "Uploading incremental shard %s/%s (%s rows)...",
            batch_index,
            total_batches,
            len(current_rows),
        )
        await _upload_rows_as_parquet(
            api=api,
            repo_id=repo_id,
            token=token,
            rows=current_rows,
            shard_index=batch_index,
            total_batches=total_batches,
        )
        hf_url_set.update(current_urls)
        append_url_cache(cache_path, list(current_urls))


async def _upload_rows_as_parquet(
    *,
    api: HfApi,
    repo_id: str,
    token: str,
    rows: list,
    shard_index: int,
    total_batches: int,
):
    data_dict = {
        "id": [row["id"] for row in rows],
        "text": [row["text"] for row in rows],
        "url": [row["url"] for row in rows],
        "language": [row["language"] for row in rows],
    }

    hf_dataset = Dataset.from_dict(data_dict)
    parquet_path = f"data/hf_export/train-{shard_index:06d}-of-{total_batches:06d}.parquet"
    hf_dataset.to_parquet(parquet_path)

    repo_path = f"data/train-{shard_index:06d}-of-{total_batches:06d}.parquet"
    api.upload_file(
        path_or_fileobj=parquet_path,
        path_in_repo=repo_path,
        repo_id=repo_id,
        repo_type="dataset",
        token=token,
    )

    os.remove(parquet_path)

async def upload_dataset(repo_id: str, batch_size: int, token: str, private: bool = True):
    """
    Query training_documents, yield in batches, and append to HF.
    """
    
    login(token=token)
    api = HfApi()

    # Create repo if it doesn't exist
    try:
        api.repo_info(repo_id, repo_type="dataset")
        print(f"Repository {repo_id} exists. Appending to it.")
    except Exception:
        print(f"Creating repository {repo_id}...")
        api.create_repo(repo_id, repo_type="dataset", private=private)

    storage = EnvStorageService()
    await storage.initialize()
    session = storage.create_session()

    try:
        total_docs = await get_total_count(session)
        print(f"Total documents to upload: {total_docs}")

        if total_docs == 0:
            print("No documents found in training_documents.")
            return

        # Use an async generator to fetch in batches using LIMIT and OFFSET
        # For very large tables, keyset pagination (WHERE id > last_id) is better,
        # but since this is a one-off upload of ~450k rows, LIMIT/OFFSET is fine and simpler.
        offset = 0
        batch_num = 1
        
        while offset < total_docs:
            logger.info(f"Fetching batch {batch_num} (offset {offset}, size {batch_size})...")
            
            # Select only requested columns + mapping id to uuid
            query = """
                SELECT 
                    id, 
                    text, 
                    url, 
                    language 
                FROM training_documents
                ORDER BY id
                LIMIT $1 OFFSET $2
            """
            
            rows = await session.service._db.fetch(query, batch_size, offset)
            
            if not rows:
                break
                
            print(f"  Got {len(rows)} records. Preparing HF dataset...")
            
            # Convert to dictionary format required by datasets
            data_dict = {
                "id": [row["id"] for row in rows],
                "text": [row["text"] for row in rows],
                "url": [row["url"] for row in rows],
                "language": [row["language"] for row in rows],
            }
            
            # Create HF Dataset object
            hf_dataset = Dataset.from_dict(data_dict)
            
            # Push to hub
            # If batch_num > 1, we must append to the existing dataset files
            # The easiest way for a raw dataset with `datasets` is to simply write out parquet
            # files named generically (e.g., data_001.parquet, data_002.parquet) to the repo.
            
            file_name = f"data/train-{batch_num:04d}-of-UNKNOWN.parquet"
            print(f"  Pushing {file_name} to {repo_id}...")
            
            # Push to hub appending to data directory
            hf_dataset.push_to_hub(
                repo_id=repo_id,
                config_name="default",
                split="train",
                token=token,
                private=private,
                # appending is tricky with standard push_to_hub if not careful,
                # actually push_to_hub overwrites the split. 
                # Let's write locally and use HfApi to upload the file to avoid loading all into memory.
            )
            # Actually push_to_hub replaces the whole dataset! We must export to parquet and upload files.
            
            offset += batch_size
            batch_num += 1

    finally:
        await storage.close()

async def upload_dataset_file_based(
    repo_id: str,
    batch_size: int,
    token: str,
    private: bool = True,
    incremental: bool | None = None,
    hf_url_cache: str = "data/hf_url_cache.jsonl",
    refresh_cache: bool = True,
    max_batches: int | None = None,
):
    """
    Query training_documents, write to local parquet, and upload file by file.
    This avoids `push_to_hub` overwriting the whole dataset on each batch.
    """
    login(token=token)
    api = HfApi()

    # Create repo if it doesn't exist
    repo_exists = True
    try:
        api.repo_info(repo_id, repo_type="dataset")
        print(f"Repository {repo_id} exists.")
    except Exception as e:
        repo_exists = False
        print(f"Creating repository {repo_id}...")
        api.create_repo(repo_id, repo_type="dataset", private=private)

    logger.info("Initializing storage service...")
    storage = EnvStorageService()
    await storage.initialize()
    session = storage.create_session()
    
    os.makedirs("data/hf_export", exist_ok=True)

    try:
        if incremental is None:
            incremental = repo_exists

        if incremental:
            hf_url_set: set[str] = set()
            if repo_exists:
                if refresh_cache:
                    try:
                        hf_url_set = refresh_url_cache_from_hf(repo_id, hf_url_cache)
                    except Exception as exc:
                        logger.warning("Failed to refresh HF URL cache: %s", exc)
                        hf_url_set = load_url_cache(hf_url_cache)
                else:
                    hf_url_set = load_url_cache(hf_url_cache)
            else:
                if refresh_cache:
                    write_url_cache(hf_url_cache, set())

            max_index = get_max_shard_index(api, repo_id) if repo_exists else 0
            new_count = await count_new_rows(session, hf_url_set, batch_size, max_batches)

            if new_count == 0:
                logger.info("No new URLs found; nothing to upload.")
                return

            new_batches = math.ceil(new_count / batch_size)
            new_total = max_index + new_batches

            logger.info(
                "Incremental upload: new rows=%s, new batches=%s, start_index=%s, new_total=%s",
                new_count,
                new_batches,
                max_index + 1,
                new_total,
            )

            await upload_incremental_batches(
                session=session,
                api=api,
                repo_id=repo_id,
                token=token,
                batch_size=batch_size,
                hf_url_set=hf_url_set,
                cache_path=hf_url_cache,
                start_index=max_index + 1,
                total_batches=new_total,
                max_batches=new_batches if max_batches is None else min(max_batches, new_batches),
            )

            print("\nIncremental upload complete!")
            return

        total_docs = await get_total_count(session)
        logger.info(f"Total documents to upload: {total_docs:,}")

        if total_docs == 0:
            logger.warning("No documents found in training_documents.")
            return

        if repo_exists:
            logger.warning(
                "Full upload requested on an existing repo; this will add duplicate rows."
            )

        expected_batches = (total_docs + batch_size - 1) // batch_size
        offset = 0
        batch_num = 1
        
        while offset < total_docs:
            logger.info(f"Batch {batch_num}/{expected_batches}: Fetching {batch_size:,} records (offset: {offset:,})...")
            
            # Select only requested columns + mapping id to uuid
            query = """
                SELECT 
                    id, 
                    text, 
                    url, 
                    language 
                FROM training_documents
                ORDER BY id
                LIMIT $1 OFFSET $2
            """
            
            rows = await session.service._db.fetch(query, batch_size, offset)
            
            if not rows:
                break
                
            logger.info(f"  Got {len(rows):,} records. Converting to parquet...")
            
            await _upload_rows_as_parquet(
                api=api,
                repo_id=repo_id,
                token=token,
                rows=rows,
                shard_index=batch_num,
                total_batches=expected_batches,
            )
            
            offset += batch_size
            batch_num += 1

        print("\nUpload complete!")

    finally:
        await storage.close()


async def export_to_jsonl(output_path: str, batch_size: int):
    """
    Query training_documents and write to a local JSONL file.
    """
    logger.info("Initializing storage service for export...")
    storage = EnvStorageService()
    await storage.initialize()
    session = storage.create_session()
    
    try:
        total_docs = await get_total_count(session)
        logger.info(f"Total documents to export: {total_docs:,}")

        if total_docs == 0:
            logger.warning("No documents found in training_documents.")
            return

        expected_batches = (total_docs + batch_size - 1) // batch_size
        offset = 0
        batch_num = 1
        
        logger.info(f"Exporting to local file: {output_path}...")
        
        with open(output_path, "w", encoding="utf-8") as f:
            while offset < total_docs:
                print(f"Processing batch {batch_num}/{expected_batches} (offset {offset})...")
                
                query = """
                    SELECT id, text, url, language 
                    FROM training_documents
                    ORDER BY id
                    LIMIT $1 OFFSET $2
                """
                
                rows = await session.service._db.fetch(query, batch_size, offset)
                if not rows:
                    break
                
                logger.info(f"Batch {batch_num}/{expected_batches}: Exporting {len(rows):,} records to JSONL...")
                for row in rows:
                    line = json.dumps({
                        "id": row["id"],
                        "text": row["text"],
                        "url": row["url"],
                        "language": row["language"]
                    }, ensure_ascii=False)
                    f.write(line + "\n")
                
                offset += batch_size
                batch_num += 1

        logger.info(f"🎉 Export complete: {output_path}")

    finally:
        await storage.close()


def main():
    parser = argparse.ArgumentParser(description="Upload Nepali Corpus to Hugging Face")
    parser.add_argument("--repo-id", help="Hugging Face repository ID (e.g. username/nepali-corpus)")
    parser.add_argument("--batch-size", type=int, default=50000, help="Number of records per batch (default: 50000)")
    parser.add_argument("--token", help="Hugging Face write token (defaults to local cache or HF_TOKEN env var)")
    parser.add_argument("--public", action="store_true", help="Make the repository public when creating (default: private)")
    parser.add_argument("--export-jsonl", help="Export to a local JSONL file instead of uploading")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Append only new URLs if repo exists (default when repo exists)",
    )
    parser.add_argument(
        "--no-incremental",
        action="store_false",
        dest="incremental",
        help="Disable incremental mode and upload all rows",
    )
    parser.set_defaults(incremental=None)
    parser.add_argument(
        "--hf-url-cache",
        default="data/hf_url_cache.jsonl",
        help="Path to local HF URL cache (default: data/hf_url_cache.jsonl)",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Refresh HF URL cache from the Hub (default: true)",
    )
    parser.add_argument(
        "--no-refresh-cache",
        action="store_false",
        dest="refresh_cache",
        help="Skip HF URL cache refresh and use local cache",
    )
    parser.set_defaults(refresh_cache=True)
    parser.add_argument(
        "--max-batches",
        type=int,
        help="Max number of new batches to upload in incremental mode",
    )
    
    args = parser.parse_args()

    # Priority: 1. CLI Arg, 2. Env Var, 3. Local Cache
    token = args.token or os.environ.get("HF_TOKEN") or get_token()

    if args.export_jsonl:
        asyncio.run(export_to_jsonl(args.export_jsonl, args.batch_size))

    if args.repo_id:
        if not token:
            print("Error: No Hugging Face token found.")
            print("Please login using 'huggingface-cli login' or provide --token.")
            sys.exit(1)
            
        asyncio.run(upload_dataset_file_based(
            repo_id=args.repo_id,
            batch_size=args.batch_size,
            token=token,
            private=not args.public,
            incremental=args.incremental,
            hf_url_cache=args.hf_url_cache,
            refresh_cache=args.refresh_cache,
            max_batches=args.max_batches,
        ))
    elif not args.export_jsonl:
        print("Error: Either --repo-id or --export-jsonl must be specified.")
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()

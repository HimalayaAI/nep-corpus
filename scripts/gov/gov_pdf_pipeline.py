#!/usr/bin/env python3
"""
Pipeline for discovering, downloading, and extracting Nepali PDFs from
giwmscdntwo.gov.np via Common Crawl indexes.

Steps:
  - discover: query CC index, write URL list
  - download: download PDFs into a folder
  - extract: convert PDFs to markdown via likhit, fallback to OCR (optional)

Designed for small-batch testing first, then scaled runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urlparse

import requests

# Ensure project root is on sys.path for local imports
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nepali_corpus.core.utils.normalize import normalize_text, devanagari_ratio
from nepali_corpus.core.models.documents import TrainingDocument
from nepali_corpus.core.services.storage.env_storage import EnvStorageService


LIKIT_SRC = Path(__file__).resolve().parents[2] / "tools" / "likhit" / "src"
if LIKIT_SRC.exists():
    sys.path.insert(0, str(LIKIT_SRC))

try:
    from likhit.core import convert as likhit_convert
except Exception:
    likhit_convert = None


DEFAULT_PREFIXES = [
    "/media/pdf_upload/",
    "/media/files/",
    "/media/app/public/",
]


def fetch_latest_cc_index() -> str:
    resp = requests.get("https://index.commoncrawl.org/collinfo.json", timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return "CC-MAIN-2024-46"
    return data[0]["id"]


def iter_cc_urls(domain: str, index_id: str) -> Iterable[str]:
    url = f"https://index.commoncrawl.org/{index_id}-index"
    params = {"url": f"{domain}/*", "output": "json"}
    resp = requests.get(url, params=params, stream=True, timeout=120)
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        u = rec.get("url")
        if u:
            yield u


def write_urls(
    urls: Iterable[str],
    output: Path,
    limit: Optional[int] = None,
    dedupe: bool = True,
) -> int:
    seen = set()
    count = 0
    with output.open("w", encoding="utf-8") as f:
        for url in urls:
            if dedupe:
                if url in seen:
                    continue
                seen.add(url)
            f.write(url + "\n")
            count += 1
            if limit and count >= limit:
                break
    return count


def filter_urls(
    urls: Iterable[str],
    prefixes: List[str],
    require_pdf: bool = True,
) -> Iterable[str]:
    for url in urls:
        try:
            path = urlparse(url).path
        except Exception:
            continue
        if prefixes and not any(path.startswith(p) for p in prefixes):
            continue
        if require_pdf and not path.lower().endswith(".pdf"):
            continue
        yield url


def discover(args: argparse.Namespace) -> None:
    index_id = args.cc_index or fetch_latest_cc_index()
    prefixes = args.prefix or DEFAULT_PREFIXES
    output = Path(args.output)
    urls = filter_urls(iter_cc_urls(args.domain, index_id), prefixes)
    count = write_urls(urls, output, limit=args.limit, dedupe=not args.no_dedupe)
    print(f"Wrote {count} URLs to {output}")


def safe_filename(url: str) -> str:
    path = urlparse(url).path
    name = os.path.basename(path)
    if not name:
        return hashlib.md5(url.encode("utf-8")).hexdigest() + ".pdf"
    return name


def download(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    urls = Path(args.input).read_text(encoding="utf-8").splitlines()
    if args.limit:
        urls = urls[: args.limit]

    manifest = Path(args.manifest) if args.manifest else None
    if manifest:
        manifest.parent.mkdir(parents=True, exist_ok=True)

    with (manifest.open("a", encoding="utf-8") if manifest else open(os.devnull, "w")) as mf:
        for idx, url in enumerate(urls, start=1):
            name = safe_filename(url)
            dest = out_dir / name
            if dest.exists() and not args.overwrite:
                if manifest:
                    mf.write(json.dumps({"url": url, "file": str(dest), "status": "exists"}) + "\n")
                continue
            try:
                r = requests.get(url, stream=True, timeout=120)
                r.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r.iter_content(1024 * 1024):
                        if chunk:
                            f.write(chunk)
                if manifest:
                    mf.write(json.dumps({"url": url, "file": str(dest), "status": "ok"}) + "\n")
            except Exception as exc:
                if manifest:
                    mf.write(json.dumps({"url": url, "file": str(dest), "status": "error", "error": str(exc)}) + "\n")
            if args.sleep:
                time.sleep(args.sleep)
            if args.limit and idx >= args.limit:
                break


def ocr_pdf_text(path: Path, lang: str, max_pages: int) -> str:
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image, ImageOps
    except Exception:
        return ""

    doc = fitz.open(path)
    pages = []
    for idx, page in enumerate(doc):
        if max_pages and idx >= max_pages:
            break
        pix = page.get_pixmap(dpi=300)
        mode = "RGBA" if pix.alpha else "RGB"
        img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
        img = ImageOps.grayscale(img)
        img = ImageOps.autocontrast(img)
        pages.append(pytesseract.image_to_string(img, lang=lang))
    doc.close()
    return "\n\n".join(pages).strip()


def extract(args: argparse.Namespace) -> None:
    if likhit_convert is None:
        raise SystemExit("likhit is not available. Ensure tools/likhit is present and importable.")

    in_dir = Path(args.input)
    url_map = {}
    if in_dir.is_file() and in_dir.suffix == ".jsonl":
        files = []
        with in_dir.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                file_path = rec.get("file")
                url = rec.get("url")
                if file_path:
                    p = Path(file_path)
                    files.append(p)
                    if url:
                        url_map[str(p)] = url
        if args.limit:
            files = files[: args.limit]
    else:
        files = sorted([p for p in in_dir.glob("*.pdf")])
        if args.limit:
            files = files[: args.limit]

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as out_f:
        for file_path in files:
            text = ""
            extraction = "likhit"
            try:
                text = likhit_convert(str(file_path)) or ""
            except Exception:
                text = ""
            text_norm = normalize_text(text)
            ratio = devanagari_ratio(text_norm)
            if args.ocr and (len(text_norm) < args.min_chars or ratio < args.min_deva_ratio):
                ocr_text = ocr_pdf_text(file_path, args.ocr_lang, args.ocr_max_pages)
                ocr_norm = normalize_text(ocr_text)
                ocr_ratio = devanagari_ratio(ocr_norm)
                if len(ocr_norm) > len(text_norm) or ocr_ratio > ratio + 0.3:
                    text_norm = ocr_norm
                    ratio = ocr_ratio
                    extraction = "ocr"

            if len(text_norm) < args.min_chars or ratio < args.min_deva_ratio:
                continue

            url = url_map.get(str(file_path))
            doc_id_seed = url or str(file_path)
            doc_id = hashlib.md5(doc_id_seed.encode("utf-8")).hexdigest()
            record = {
                "doc_id": doc_id,
                "url": url,
                "source": args.source,
                "language": "ne",
                "text": text_norm,
                "deva_ratio": ratio,
                "chars": len(text_norm),
                "extraction": extraction,
                "file": str(file_path),
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")


def upsert_training(jsonl_path: Path, source_id: str, category: str) -> int:
    import asyncio

    async def _sync() -> int:
        storage = EnvStorageService()
        await storage.initialize()
        session = storage.create_session()
        docs = []
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                url = rec.get("url") or rec.get("file")
                docs.append(
                    TrainingDocument(
                        id=rec["doc_id"],
                        url=url,
                        source_id=source_id,
                        source_name=source_id,
                        language=rec.get("language", "ne"),
                        text=rec.get("text", ""),
                        category=category,
                        content_type="pdf",
                    )
                )
        await session.store_training_documents(docs)
        await storage.close()
        return len(docs)

    return asyncio.run(_sync())


def run_all(args: argparse.Namespace) -> None:
    urls_path = Path(args.urls)
    if not urls_path.exists():
        discover_args = argparse.Namespace(
            domain=args.domain,
            cc_index=args.cc_index,
            prefix=args.prefix,
            limit=args.discover_limit,
            no_dedupe=args.no_dedupe,
            output=str(urls_path),
        )
        discover(discover_args)

    download_args = argparse.Namespace(
        input=str(urls_path),
        out_dir=args.out_dir,
        manifest=args.manifest,
        limit=args.download_limit,
        sleep=args.sleep,
        overwrite=args.overwrite,
    )
    download(download_args)

    extract_args = argparse.Namespace(
        input=args.manifest,
        output=args.output,
        source=args.source,
        min_chars=args.min_chars,
        min_deva_ratio=args.min_deva_ratio,
        ocr=args.ocr,
        ocr_lang=args.ocr_lang,
        ocr_max_pages=args.ocr_max_pages,
        limit=args.extract_limit,
    )
    extract(extract_args)

    if args.db:
        count = upsert_training(Path(args.output), args.source, args.category)
        print(f"Upserted {count} documents into training_documents")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="giwmscdntwo.gov.np PDF pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_discover = sub.add_parser("discover", help="Discover PDF URLs via Common Crawl")
    p_discover.add_argument("--domain", default="giwmscdntwo.gov.np")
    p_discover.add_argument("--cc-index", help="CC index ID (default: latest)")
    p_discover.add_argument("--prefix", action="append", help="Path prefix filter (repeatable)")
    p_discover.add_argument("--limit", type=int, help="Limit number of URLs")
    p_discover.add_argument("--no-dedupe", action="store_true")
    p_discover.add_argument("--output", default="data/giwmscdntwo_urls.txt")

    p_download = sub.add_parser("download", help="Download PDFs from URL list")
    p_download.add_argument("--input", required=True, help="URL list file")
    p_download.add_argument("--out-dir", default="data/giwmscdntwo_pdfs")
    p_download.add_argument("--manifest", default="data/giwmscdntwo_downloads.jsonl")
    p_download.add_argument("--limit", type=int, help="Limit number of downloads")
    p_download.add_argument("--sleep", type=float, default=0.0, help="Sleep between downloads (seconds)")
    p_download.add_argument("--overwrite", action="store_true")

    p_extract = sub.add_parser("extract", help="Extract Markdown/ Text from PDFs")
    p_extract.add_argument("--input", required=True, help="PDF directory or manifest.jsonl")
    p_extract.add_argument("--output", default="data/giwmscdntwo_text.jsonl")
    p_extract.add_argument("--source", default="gov_cdn:giwmscdntwo")
    p_extract.add_argument("--min-chars", type=int, default=200)
    p_extract.add_argument("--min-deva-ratio", type=float, default=0.2)
    p_extract.add_argument("--ocr", action="store_true", help="Enable OCR fallback (tesseract)")
    p_extract.add_argument("--ocr-lang", default="nep+eng")
    p_extract.add_argument("--ocr-max-pages", type=int, default=0, help="0 = all pages")
    p_extract.add_argument("--limit", type=int, help="Limit number of PDFs to extract")

    p_run = sub.add_parser("run", help="Discover + download + extract (+ optional DB upsert)")
    p_run.add_argument("--domain", default="giwmscdntwo.gov.np")
    p_run.add_argument("--cc-index")
    p_run.add_argument("--prefix", action="append", help="Path prefix filter (repeatable)")
    p_run.add_argument("--no-dedupe", action="store_true")
    p_run.add_argument("--discover-limit", type=int, help="Limit URL discovery")
    p_run.add_argument("--urls", default="data/giwmscdntwo_urls.txt")
    p_run.add_argument("--out-dir", default="data/giwmscdntwo_pdfs")
    p_run.add_argument("--manifest", default="data/giwmscdntwo_downloads.jsonl")
    p_run.add_argument("--download-limit", type=int, help="Limit downloads")
    p_run.add_argument("--sleep", type=float, default=0.0)
    p_run.add_argument("--overwrite", action="store_true")
    p_run.add_argument("--output", default="data/giwmscdntwo_text.jsonl")
    p_run.add_argument("--source", default="gov_cdn:giwmscdntwo")
    p_run.add_argument("--category", default="gov_pdf")
    p_run.add_argument("--min-chars", type=int, default=200)
    p_run.add_argument("--min-deva-ratio", type=float, default=0.2)
    p_run.add_argument("--ocr", action="store_true")
    p_run.add_argument("--ocr-lang", default="nep+eng")
    p_run.add_argument("--ocr-max-pages", type=int, default=0)
    p_run.add_argument("--extract-limit", type=int, help="Limit extraction")
    p_run.add_argument("--db", action="store_true", help="Upsert into training_documents")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.cmd == "discover":
        discover(args)
    elif args.cmd == "download":
        download(args)
    elif args.cmd == "extract":
        extract(args)
    elif args.cmd == "run":
        run_all(args)
    else:
        raise SystemExit(f"Unknown command: {args.cmd}")


if __name__ == "__main__":
    main()

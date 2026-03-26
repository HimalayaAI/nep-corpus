#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, List, Set
from urllib.parse import urlparse

import requests
import yaml


def fetch_cc_indexes(latest: int) -> List[str]:
    resp = requests.get("https://index.commoncrawl.org/collinfo.json", timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return ["CC-MAIN-2024-46"]
    return [row["id"] for row in data[:latest]]


def iter_cc_hosts(index_id: str, pattern: str) -> Iterable[str]:
    url = f"https://index.commoncrawl.org/{index_id}-index"
    resp = requests.get(url, params={"url": pattern, "output": "json"}, stream=True, timeout=120)
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        u = rec.get("url")
        if not u:
            continue
        host = urlparse(u).netloc.lower()
        if host:
            yield host


def load_registry_hosts(path: Path) -> Set[str]:
    hosts: Set[str] = set()
    if not path.exists():
        return hosts
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return hosts
    for row in data:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        if not url:
            continue
        host = urlparse(url).netloc.lower()
        if host.endswith(".gov.np"):
            hosts.add(host)
    return hosts


def load_jsonl_hosts(path: Path) -> Set[str]:
    hosts: Set[str] = set()
    if not path.exists():
        return hosts
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        host = obj.get("host") or obj.get("url")
        if not host:
            continue
        if host.startswith("http"):
            host = urlparse(host).netloc
        host = host.lower()
        if host.endswith(".gov.np"):
            hosts.add(host)
    return hosts


def write_jsonl(hosts: Iterable[str], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as f:
        for host in sorted(set(hosts)):
            payload = {
                "id": f"gov_discovery:{host}",
                "name": host,
                "url": f"https://{host}",
                "source_type": "government",
                "category": "gov_discovery",
                "scraper_class": "miner",
                "is_discovery": True,
                "enabled": True,
                "language": "ne",
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Build gov.np discovery JSONL")
    parser.add_argument("--output", default="sources/gov_discovery_registry.jsonl")
    parser.add_argument("--registry", default="sources/govt_sources_registry.yaml")
    parser.add_argument("--cc-hosts", default="sources/gov_np_hosts.jsonl")
    parser.add_argument("--cc-index", action="append", help="Common Crawl index ID (repeatable)")
    parser.add_argument("--cc-latest", type=int, default=0, help="Fetch latest N CC indexes")
    parser.add_argument("--pattern", default="*.gov.np/*")
    parser.add_argument("--no-cc", action="store_true", help="Skip Common Crawl lookup")
    args = parser.parse_args()

    hosts: Set[str] = set()
    hosts |= load_registry_hosts(Path(args.registry))
    hosts |= load_jsonl_hosts(Path(args.cc_hosts))

    if not args.no_cc:
        indexes = args.cc_index or []
        if args.cc_latest and not indexes:
            indexes = fetch_cc_indexes(args.cc_latest)
        for idx in indexes:
            for host in iter_cc_hosts(idx, args.pattern):
                if host.endswith(".gov.np"):
                    hosts.add(host)

    count = write_jsonl(hosts, Path(args.output))
    print(f"Wrote {count} hosts to {args.output}")


if __name__ == "__main__":
    main()

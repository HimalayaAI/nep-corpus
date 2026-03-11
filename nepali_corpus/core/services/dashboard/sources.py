from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote_plus

import yaml

from nepali_corpus.core.services.scrapers.news_rss_scraper import FEEDS as NEWS_FEEDS
from nepali_corpus.core.services.scrapers.ekantipur_scraper import PROVINCES as EKANTIPUR_PROVINCES, BASE_URL as EKANTIPUR_BASE


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _titleize(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _govt_category(group_name: str) -> str:
    lowered = group_name.lower()
    if "education" in lowered:
        return "Education"
    if "health" in lowered:
        return "Health"
    if "reference" in lowered:
        return "Reference"
    return "Gov"


def load_government_sources() -> List[Dict[str, Any]]:
    sources_path = _repo_root() / "sources" / "govt_sources_registry.yaml"
    data = _load_yaml(sources_path)
    sources: List[Dict[str, Any]] = []

    def _rec_load(obj: Any, group_name: str, group_path: List[str]):
        if isinstance(obj, list):
            category = _govt_category(" ".join(group_path))
            for item in obj:
                if not isinstance(item, dict):
                    continue
                source_id = item.get("id")
                name = item.get("name") or source_id or "Unknown Source"
                url = item.get("base_url") or ""
                sources.append(
                    {
                        "id": source_id or name,
                        "name": name,
                        "url": url,
                        "category": category,
                        "group": group_name,
                        "group_label": _titleize(group_name),
                        "source_type": "Government",
                        "format": "PDF+HTML",
                    }
                )
        elif isinstance(obj, dict):
            for k, v in obj.items():
                # Skip top-level config keys that aren't source groups
                if k in ["scraper_class", "url_pattern", "total_districts", "priority_districts", "poll_interval_mins"]:
                    continue
                _rec_load(v, k, group_path + [k])

    _rec_load(data, "government", ["government"])
    return sources


def load_news_sources() -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    for feed_id, meta in NEWS_FEEDS.items():
        sources.append(
            {
                "id": feed_id,
                "name": meta.get("name", feed_id),
                "url": meta.get("url", ""),
                "category": "News",
                "group": "news_rss",
                "group_label": "News RSS",
                "source_type": "News",
                "format": "RSS",
            }
        )
    sources.append(
        {
            "id": "ekantipur_national",
            "name": "Ekantipur National",
            "url": EKANTIPUR_BASE,
            "category": "News",
            "group": "news_html",
            "group_label": "News HTML",
            "source_type": "News",
            "format": "HTML",
        }
    )
    for key, info in EKANTIPUR_PROVINCES.items():
        sources.append(
            {
                "id": f"ekantipur_{key}",
                "name": f"Ekantipur {info['name']}",
                "url": f"{EKANTIPUR_BASE}{info['path']}",
                "category": "News",
                "group": "news_html",
                "group_label": "News HTML",
                "source_type": "News",
                "format": "HTML",
            }
        )
    return sources


def load_social_sources() -> List[Dict[str, Any]]:
    sources_path = _repo_root() / "sources" / "social_sources.yaml"
    data = _load_yaml(sources_path)
    nitter = ""
    instances = data.get("nitter_instances", [])
    if isinstance(instances, list) and instances:
        nitter = instances[0].get("url", "")

    sources: List[Dict[str, Any]] = []

    accounts = data.get("accounts", [])
    if isinstance(accounts, list):
        for account in accounts:
            username = account.get("username")
            if not username:
                continue
            name = account.get("name") or username
            category = account.get("category") or "social"
            url = f"{nitter}/{username}" if nitter else f"https://twitter.com/{username}"
            sources.append(
                {
                    "id": f"social:{username}",
                    "name": name,
                    "url": url,
                    "category": "Social",
                    "group": category,
                    "group_label": _titleize(category),
                    "source_type": "Social",
                    "format": "Nitter",
                }
            )

    hashtags = data.get("hashtags", [])
    if isinstance(hashtags, list):
        for tag in hashtags:
            label = tag.get("tag")
            if not label:
                continue
            name = tag.get("name") or f"#{label}"
            query = quote_plus(f"#{label}")
            url = f"{nitter}/search?f=tweets&q={query}" if nitter else f"https://twitter.com/search?q={query}"
            sources.append(
                {
                    "id": f"hashtag:{label}",
                    "name": name,
                    "url": url,
                    "category": "Social",
                    "group": "hashtag",
                    "group_label": "Hashtag",
                    "source_type": "Social",
                    "format": "Nitter",
                }
            )

    searches = data.get("searches", [])
    if isinstance(searches, list):
        for item in searches:
            query = item.get("query")
            if not query:
                continue
            name = item.get("name") or query
            encoded = quote_plus(query)
            url = f"{nitter}/search?f=tweets&q={encoded}" if nitter else f"https://twitter.com/search?q={encoded}"
            sources.append(
                {
                    "id": f"search:{query}",
                    "name": name,
                    "url": url,
                    "category": "Social",
                    "group": "search",
                    "group_label": "Search",
                    "source_type": "Social",
                    "format": "Nitter",
                }
            )

    return sources


@lru_cache(maxsize=1)
def load_source_catalog() -> List[Dict[str, Any]]:
    sources: List[Dict[str, Any]] = []
    sources.extend(load_government_sources())
    sources.extend(load_news_sources())
    sources.extend(load_social_sources())
    return sources


def get_sources(refresh: bool = False) -> List[Dict[str, Any]]:
    if refresh:
        load_source_catalog.cache_clear()
    return [dict(item) for item in load_source_catalog()]


__all__ = ["get_sources"]

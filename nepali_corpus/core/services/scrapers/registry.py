from __future__ import annotations

from typing import Dict, List, Optional

import logging

import yaml

from ...models.government_schemas import MinistryConfig, RegistryEntry

logger = logging.getLogger(__name__)


def load_registry(path: str, groups: Optional[List[str]] = None) -> List[RegistryEntry]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to load registry from {path}: {e}")
        return []

    entries: List[RegistryEntry] = []
    allowed = set(g.strip() for g in groups) if groups else None
    matched_groups: set[str] = set()

    def _rec_load(obj: Any, group_name: str):
        if allowed is not None and group_name not in allowed:
            # If not allowed, we don't recurse further if we explicitly filtered by groups
            # However, usually groups are top-level. 
            # We'll stick to a simple filter for now.
            pass

        if isinstance(obj, list):
            if allowed is not None and group_name not in allowed:
                return
            if allowed is not None:
                matched_groups.add(group_name)
                
            for item in obj:
                if not isinstance(item, dict):
                    continue
                scraper_class = item.get("scraper_class", "") or ""
                if scraper_class == "nrb_scraper":
                    scraper_class = "regulatory"
                
                entries.append(
                    RegistryEntry(
                        source_id=item.get("id"),
                        name=item.get("name"),
                        name_ne=item.get("name_ne"),
                        base_url=item.get("base_url"),
                        endpoints=item.get("endpoints", {}),
                        scraper_class=scraper_class,
                        priority=item.get("priority", 3),
                        poll_interval_mins=item.get("poll_interval_mins", 180),
                    )
                )
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k in ["scraper_class", "url_pattern", "total_districts", "priority_districts", "poll_interval_mins"]:
                    continue
                _rec_load(v, k)

    _rec_load(data, "root")

    if allowed is not None and not matched_groups:
        logger.warning("No registry groups matched: %s", sorted(allowed))
    if allowed is not None and not entries:
        logger.warning("No registry entries found for groups: %s", sorted(allowed))
    return entries


def registry_to_ministry_configs(entries: List[RegistryEntry]) -> Dict[str, MinistryConfig]:
    configs: Dict[str, MinistryConfig] = {}
    skipped: Dict[str, int] = {}
    for entry in entries:
        if entry.scraper_class != "ministry_generic":
            if entry.scraper_class:
                skipped[entry.scraper_class] = skipped.get(entry.scraper_class, 0) + 1
            continue
        if not entry.source_id or not entry.base_url:
            continue
        configs[entry.source_id] = MinistryConfig(
            source_id=entry.source_id,
            name=entry.name or entry.source_id,
            name_ne=entry.name_ne or entry.source_id,
            base_url=entry.base_url,
            endpoints=entry.endpoints,
            priority=entry.priority,
        )
    if skipped and not configs:
        logger.info(
            "Registry entries use non-ministry scraper classes: %s",
            ", ".join(f"{k}({v})" for k, v in sorted(skipped.items())),
        )
    return configs

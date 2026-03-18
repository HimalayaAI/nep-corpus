import pytest
from nepali_corpus.core.services.scrapers.registry import load_registry

def test_load_registry_with_groups(tmp_path):
    yaml_text = """
- id: a1
  name: Group A One
  name_ne: समूह ए
  url: https://example.com
  category: group_a
  endpoints: {}
  scraper_class: ministry_generic
  priority: 1
  poll_interval_mins: 60
- id: b1
  name: Group B One
  name_ne: समूह बी
  url: https://example.org
  category: group_b
  endpoints: {}
  scraper_class: ministry_generic
  priority: 2
  poll_interval_mins: 120
"""
    path = tmp_path / "registry.yaml"
    path.write_text(yaml_text, encoding="utf-8")

    entries = load_registry(str(path), groups=["group_a"])
    assert len(entries) == 1
    assert entries[0].source_id == "a1"
    assert entries[0].base_url == "https://example.com"

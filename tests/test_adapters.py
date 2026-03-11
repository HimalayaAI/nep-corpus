from dataclasses import dataclass

from nepali_corpus.core.services.scrapers.news_rss_scraper import article_to_raw as rss_to_raw
from nepali_corpus.core.services.scrapers.ekantipur_scraper import article_to_raw as ek_to_raw
from nepali_corpus.core.services.scrapers.govt_scraper import post_to_raw as govt_to_raw
from nepali_corpus.core.services.scrapers.dao_scraper import post_to_raw as dao_to_raw


@dataclass
class DummyRSS:
    id: str
    title: str
    url: str
    source_id: str
    source_name: str
    language: str
    published_at: str = "2025-01-01T00:00:00"
    summary: str = "summary"
    content: str = "content"
    author: str = "author"
    categories: list = None
    fetched_at: str = "2025-01-01T01:00:00"


@dataclass
class DummyEk:
    id: str
    title: str
    url: str
    province: str
    source_id: str
    source_name: str
    published_at: str = "2025-01-02T00:00:00"
    image_url: str = "http://image"
    summary: str = "summary"
    language: str = "ne"
    scraped_at: str = "2025-01-02T01:00:00"


@dataclass
class DummyGovt:
    id: str
    title: str
    url: str
    source_id: str
    source_name: str
    source_domain: str
    date_bs: str = "2081-09-15"
    date_ad: object = None
    category: str = "press_release"
    language: str = "ne"
    has_attachment: bool = True
    attachment_urls: list = None
    scraped_at: str = "2025-01-03T01:00:00"


@dataclass
class DummyDAO:
    id: str
    title: str
    url: str
    district: str
    province: str
    date_bs: str = "2081-09-15"
    category: str = "notice-ne"
    has_attachment: bool = False
    source: str = "dao_kathmandu"
    source_name: str = "DAO Kathmandu"
    scraped_at: str = "2025-01-04T01:00:00"


def test_rss_adapter_mapping():
    art = DummyRSS(id="1", title="Title", url="http://x", source_id="rss", source_name="RSS", language="ne", categories=["tag"])
    rec = rss_to_raw(art)
    assert rec.source_id == "rss"
    assert rec.url == "http://x"
    assert rec.tags == ["tag"]


def test_ekantipur_adapter_mapping():
    art = DummyEk(id="1", title="Title", url="http://x", province="Koshi", source_id="ek", source_name="Ek")
    rec = ek_to_raw(art)
    assert rec.province == "Koshi"
    assert rec.language == "ne"


def test_govt_adapter_mapping():
    post = DummyGovt(id="1", title="Title", url="http://x", source_id="mof", source_name="MOF", source_domain="mof.gov.np")
    rec = govt_to_raw(post)
    assert rec.date_bs == "2081-09-15"
    assert rec.category == "press_release"


def test_dao_adapter_mapping():
    post = DummyDAO(id="1", title="Title", url="http://x", district="Kathmandu", province="Bagmati")
    rec = dao_to_raw(post)
    assert rec.district == "Kathmandu"
    assert rec.province == "Bagmati"
    assert rec.language == "ne"

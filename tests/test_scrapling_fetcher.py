"""Tests for the Scrapling wrapper and its fallback wiring.

All fetcher engines are monkeypatched with fakes so the suite runs without
scrapling installed.
"""
from unittest import mock

import pytest

import nepali_corpus.core.utils.scrapling_fetcher as sf
from nepali_corpus.core.utils.scrapling_fetcher import is_likely_blocked


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status


BIG_HTML = b"<html><body>" + (b"x" * 3000) + b"</body></html>"
CLOUDFLARE = b"<html><body>Just a moment... Checking your browser before accessing.</body></html>"


# ── is_likely_blocked heuristics ─────────────────────────────────────────────


def test_blocked_on_none_and_empty():
    assert is_likely_blocked(None)
    assert is_likely_blocked(b"")
    assert is_likely_blocked("")


def test_blocked_on_short_body():
    assert is_likely_blocked(b"<html><body>hi</body></html>")


@pytest.mark.parametrize(
    "marker",
    [b"Just a moment", b"Ray ID", b"cf-browser-verification", b"Access denied"],
)
def test_blocked_on_fingerprints(marker):
    page = b"<html><body>" + marker + (b" " * 2500) + b"</body></html>"
    assert is_likely_blocked(page)


def test_not_blocked_on_large_clean_html():
    assert not is_likely_blocked(BIG_HTML)


# ── stealth_fetch / dynamic_fetch engine wiring ─────────────────────────────


def _restore(module, **flags):
    for name, value in flags.items():
        setattr(module, name, value)
    return module


def test_stealth_fetch_returns_body_bytes(monkeypatch):
    fake = mock.Mock()
    fake.fetch.return_value = FakeResponse(BIG_HTML, 200)
    monkeypatch.setattr(sf, "_StealthyFetcher", fake)
    monkeypatch.setattr(sf, "HAS_STEALTH", True)

    out = sf.stealth_fetch("https://example.com")
    assert out == BIG_HTML
    args, kwargs = fake.fetch.call_args
    assert args == ("https://example.com",)
    assert kwargs["timeout"] == 30 * 1000
    assert kwargs["headless"] is True


def test_stealth_fetch_none_when_unavailable(monkeypatch):
    monkeypatch.setattr(sf, "HAS_STEALTH", False)
    assert sf.stealth_fetch("https://example.com") is None


def test_stealth_fetch_non_200(monkeypatch):
    fake = mock.Mock()
    fake.fetch.return_value = FakeResponse(BIG_HTML, 403)
    monkeypatch.setattr(sf, "_StealthyFetcher", fake)
    monkeypatch.setattr(sf, "HAS_STEALTH", True)
    assert sf.stealth_fetch("https://example.com") is None


def test_dynamic_fetch_with_wait_selector(monkeypatch):
    fake = mock.Mock()
    fake.fetch.return_value = FakeResponse(BIG_HTML, 200)
    monkeypatch.setattr(sf, "_DynamicFetcher", fake)
    monkeypatch.setattr(sf, "HAS_DYNAMIC", True)

    out = sf.dynamic_fetch("https://example.com", timeout=10, wait_selector="article")
    assert out == BIG_HTML
    kwargs = fake.fetch.call_args.kwargs
    assert kwargs["wait_selector"] == "article"
    assert kwargs["timeout"] == 10 * 1000


def test_dynamic_fetch_falls_back_to_stealth(monkeypatch):
    fake = mock.Mock()
    fake.fetch.return_value = FakeResponse(BIG_HTML, 200)
    monkeypatch.setattr(sf, "_DynamicFetcher", None)
    monkeypatch.setattr(sf, "HAS_DYNAMIC", False)
    monkeypatch.setattr(sf, "_StealthyFetcher", fake)
    monkeypatch.setattr(sf, "HAS_STEALTH", True)

    out = sf.dynamic_fetch("https://example.com")
    assert out == BIG_HTML
    assert fake.fetch.called


def test_dynamic_fetch_none_without_any_engine(monkeypatch):
    monkeypatch.setattr(sf, "HAS_DYNAMIC", False)
    monkeypatch.setattr(sf, "HAS_STEALTH", False)
    assert sf.dynamic_fetch("https://example.com") is None


def test_engine_exception_returns_none(monkeypatch):
    fake = mock.Mock()
    fake.fetch.side_effect = RuntimeError("boom")
    monkeypatch.setattr(sf, "_StealthyFetcher", fake)
    monkeypatch.setattr(sf, "HAS_STEALTH", True)
    assert sf.stealth_fetch("https://example.com") is None

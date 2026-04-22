from __future__ import annotations

from typing import Iterable


class _PyUrlSet:
    """Pure-Python fallback URL set."""

    def __init__(self) -> None:
        self._urls: set[str] = set()

    def add(self, url: str) -> None:
        if url:
            self._urls.add(url)

    def add_many(self, urls: Iterable[str]) -> int:
        before = len(self._urls)
        for url in urls:
            if url:
                self._urls.add(url)
        return len(self._urls) - before

    def contains(self, url: str) -> bool:
        return url in self._urls

    def __len__(self) -> int:
        return len(self._urls)


try:
    from rust_url_dedup import UrlSet as _RustUrlSet  # type: ignore

    class UrlSet:
        """Rust-backed URL set (falls back transparently when unavailable)."""

        def __init__(self) -> None:
            self._impl = _RustUrlSet()

        def add(self, url: str) -> None:
            self._impl.add(url)

        def add_many(self, urls: Iterable[str]) -> int:
            url_list = [u for u in urls if u]
            if url_list:
                self._impl.add_many(url_list)
            return len(url_list)

        def contains(self, url: str) -> bool:
            return self._impl.contains(url)

        def __len__(self) -> int:
            return int(self._impl.__len__())

except Exception:

    class UrlSet(_PyUrlSet):
        pass

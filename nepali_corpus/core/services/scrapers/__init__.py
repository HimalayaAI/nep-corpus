def fetch_news_rss(*args, **kwargs):
    from .news_rss_scraper import fetch_raw_records
    return fetch_raw_records(*args, **kwargs)


def fetch_ekantipur(*args, **kwargs):
    from .ekantipur_scraper import fetch_raw_records
    return fetch_raw_records(*args, **kwargs)


def fetch_govt(*args, **kwargs):
    from .govt_scraper import fetch_raw_records
    return fetch_raw_records(*args, **kwargs)


def fetch_dao(*args, **kwargs):
    from .dao_scraper import fetch_raw_records
    return fetch_raw_records(*args, **kwargs)

def fetch_regulatory(*args, **kwargs):
    from .regulatory_scraper import fetch_raw_records
    return fetch_raw_records(*args, **kwargs)


def scrape_universal(url: str, **kwargs):
    """Universal scraper - works with any URL"""
    from .universal_scraper import scrape_universal
    return scrape_universal(url, **kwargs)


__all__ = [
    "fetch_news_rss", "fetch_ekantipur", "fetch_govt", "fetch_dao",
    "fetch_regulatory", "scrape_universal"
]

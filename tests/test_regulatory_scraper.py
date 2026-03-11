from nepali_corpus.core.services.scrapers import regulatory_scraper


def test_extract_links_filters_domain_and_files():
    html = """
    <html>
      <body>
        <a href="https://sebon.gov.np/notice/123">Notice 123</a>
        <a href="/press-release/456">Press Release</a>
        <a href="https://sebon.gov.np/files/report.pdf">PDF</a>
        <a href="https://example.com/news/999">External</a>
        <a href="#section">Anchor</a>
      </body>
    </html>
    """
    links = regulatory_scraper._extract_links(html, "https://sebon.gov.np")
    urls = {url for url, _ in links}
    assert "https://sebon.gov.np/notice/123" in urls
    assert "https://sebon.gov.np/press-release/456" in urls
    assert "https://sebon.gov.np/files/report.pdf" not in urls
    assert not any("example.com" in url for url in urls)

from nepali_corpus.core.utils.enrichment import extract_text


def test_extract_text_fallback_bs4():
    html = """
    <html><head><title>Test</title></head>
    <body><h1>नेपाल</h1><p>सरकारको सूचना</p><script>var a=1;</script></body>
    </html>
    """
    text = extract_text(html, use_trafilatura=False)
    assert "नेपाल" in text
    assert "सूचना" in text
    assert "var a" not in text

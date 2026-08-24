from nepali_corpus.core.utils.enrichment import extract_text


def test_extract_text_fallback_bs4():
    # The HTML must have enough content to pass real extraction thresholds:
    # - CSS selector path: content block > 200 chars
    # - Paragraph path: p > 30 chars with devanagari_ratio > 0.3
    html = """
    <html><head><title>Test</title></head>
    <body>
      <article>
        <h1>नेपाल सरकारको महत्त्वपूर्ण सूचना</h1>
        <p>नेपाल सरकारले आज एउटा महत्त्वपूर्ण सूचना जारी गरेको छ जसमा देशका सबै नागरिकहरूलाई
        विशेष निर्देशन दिइएको छ। यो सूचना सबैले ध्यानपूर्वक पढ्नु आवश्यक छ।</p>
        <p>सरकारको निर्णय अनुसार यो नियम तुरुन्त लागु हुनेछ। सबै सम्बन्धित निकायहरूलाई
        यसको पालना गर्न अनुरोध गरिन्छ।</p>
      </article>
      <script>var a=1;</script>
    </body>
    </html>
    """
    text = extract_text(html.encode("utf-8"), "text/html", use_trafilatura=False)
    assert "नेपाल" in text
    assert "सूचना" in text
    assert "var a" not in text

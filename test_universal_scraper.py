#!/usr/bin/env python3
"""Test Universal Scraper"""

import sys
sys.path.insert(0, '.')

def test_import():
    """Test that universal scraper imports correctly"""
    print("[TEST] Importing Universal Scraper...")
    try:
        from nepali_corpus.core.services.scrapers.universal_scraper import UniversalScraper, scrape_universal
        print("  ✅ Universal scraper imports successfully")
        return True
    except Exception as e:
        print(f"  ❌ Import failed: {e}")
        return False

def test_boilerplate_detection():
    """Test boilerplate detection"""
    print("\n[TEST] Boilerplate Detection...")
    from nepali_corpus.core.services.scrapers.universal_scraper import UniversalScraper
    
    scraper = UniversalScraper()
    
    # Test cases
    tests = [
        ("Lorem ipsum dolor sit amet", False, "Lorem ipsum"),
        ("Click here to download the PDF file", False, "Download link"),
        ("नेपाल सरकारको नयाँ नियम अनुसार सबै गाडी दर्ता गर्नुपर्ने", True, "Nepali content"),
        ("", False, "Empty text"),
        ("Hello world", False, "Too short"),
        ("Notice: All vehicles must register by June 30, 2024. Ministry of Transport. Late fees apply for all registrations.", True, "Real notice"),
    ]
    
    all_pass = True
    for text, expected, desc in tests:
        result = scraper._is_real_content(text)
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_pass = False
        print(f"  {status} {desc}: {result} (expected {expected})")
    
    return all_pass

def test_pdf_detection():
    """Test PDF link detection"""
    print("\n[TEST] PDF Link Detection...")
    from nepali_corpus.core.services.scrapers.universal_scraper import UniversalScraper
    
    scraper = UniversalScraper()
    
    html = """
    <html>
    <body>
        <h1>Public Notices</h1>
        <a href="/files/notice-2024.pdf">Download Notice</a>
        <a href="/docs/circular.pdf">Circular PDF</a>
        <a href="/page.html">Regular Page</a>
    </body>
    </html>
    """.encode()
    
    pdfs = scraper._find_pdf_links(html, "https://example.gov.np")
    
    if len(pdfs) == 2:
        print("  ✅ PDF detection working (found 2 PDFs)")
        return True
    else:
        print(f"  ❌ PDF detection failed (found {len(pdfs)} PDFs: {pdfs})")
        return False

def test_devanagari_detection():
    """Test Nepali text detection"""
    print("\n[TEST] Devanagari Detection...")
    from nepali_corpus.core.services.scrapers.universal_scraper import UniversalScraper
    
    scraper = UniversalScraper()
    
    tests = [
        ("नेपाल सरकार", True),
        ("Kathmandu Notice", False),
        ("संघीय मामिला", True),
        ("Government Order 123", False),
    ]
    
    all_pass = True
    for text, expected in tests:
        result = scraper._has_devanagari(text)
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_pass = False
        print(f"  {status} '{text[:20]}...': {result}")
    
    return all_pass

def main():
    print("╔════════════════════════════════════════════╗")
    print("║    Universal Scraper - System Tests       ║")
    print("╚════════════════════════════════════════════╝\n")
    
    results = []
    
    results.append(("Import", test_import()))
    results.append(("Boilerplate Detection", test_boilerplate_detection()))
    results.append(("PDF Detection", test_pdf_detection()))
    results.append(("Devanagari Detection", test_devanagari_detection()))
    
    print("\n" + "="*50)
    print("SUMMARY")
    print("="*50)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        icon = "✅" if result else "❌"
        print(f"{icon} {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! Universal scraper is ready.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed.")
        return 1

if __name__ == "__main__":
    sys.exit(main())

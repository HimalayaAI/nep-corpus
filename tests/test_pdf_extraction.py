import asyncio
import http.server
import os
import socketserver
import threading

import pytest

from nepali_corpus.core.services.pdf.extractor import PdfJob, extract_pdfs, HAS_PYMUPDF


@pytest.mark.skipif(not HAS_PYMUPDF, reason="PyMuPDF not installed")
def test_extract_pdfs_text(tmp_path):
    import fitz  # type: ignore

    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "नेपाल परीक्षण")
    doc.save(pdf_path)
    doc.close()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A003
            return

    cwd = os.getcwd()
    os.chdir(tmp_path)
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{port}/sample.pdf"

        seen = set()

        async def seen_url(u: str) -> bool:
            return u in seen

        async def mark_url(u: str) -> None:
            seen.add(u)

        records = asyncio.run(
            extract_pdfs(
                [PdfJob(url=url, source_id="test", source_name="Test")],
                output_dir=str(tmp_path / "pdfs"),
                max_workers=1,
                seen_url=seen_url,
                mark_url=mark_url,
            )
        )
        httpd.shutdown()
        thread.join(timeout=2)

    os.chdir(cwd)
    assert len(records) == 1
    assert "नेपाल" in (records[0].content or "")
    assert records[0].raw_meta.get("pdf_path")

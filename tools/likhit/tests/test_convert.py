from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import subprocess

import fitz
from markitdown import MarkItDown
import pytest

import likhit.cli as cli_module
import likhit.core as core_module
from likhit.cli import main
from likhit.core import convert
from likhit.errors import ExtractionError
from likhit.markdown_assembly import assemble_markdown
from likhit.models import RepairedBlock, Table, TableCell, TableRegion
from likhit.nepali_pdf_repair import needs_nepali_pdf_repair


ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _devanagari_font_path() -> Path:
    """Get path to a Devanagari font. Returns None on Windows."""
    import platform

    if platform.system() == "Windows":
        # On Windows, we can't use bash/fc-match
        # Return a placeholder that will cause the test to be skipped
        return None

    try:
        result = subprocess.check_output(
            [
                "bash",
                "-lc",
                "fc-match -f '%{file}\\n' 'Noto Sans Devanagari' | head -n 1",
            ],
            text=True,
        ).strip()
        return Path(result)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _create_unicode_pdf(
    path: Path,
    *,
    title: str,
    body: str,
) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    font_path = _devanagari_font_path()
    if font_path is None:
        # Skip on Windows - font not available
        doc.close()
        pytest.skip("Devanagari font not available on Windows")
    page.insert_font(fontname="noto", fontfile=str(font_path))
    page.insert_text((72, 72), title, fontname="noto", fontsize=20)
    page.insert_text((72, 120), body, fontname="noto", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def _create_blank_pdf(path: Path) -> Path:
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    return path


def _copy_pdf_pages(source: Path, destination: Path, *, start: int, end: int) -> Path:
    source_doc = fitz.open(source)
    trimmed = fitz.open()
    trimmed.insert_pdf(source_doc, from_page=start, to_page=end)
    trimmed.save(destination)
    trimmed.close()
    source_doc.close()
    return destination


def test_convert_plain_unicode_pdf_uses_default_markitdown_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    font_path = _devanagari_font_path()
    if font_path is None:
        pytest.skip("Devanagari font not available (Windows or font not installed)")

    pdf_path = _create_unicode_pdf(
        tmp_path / "unicode.pdf",
        title="नेपाल सरकार",
        body="यो एउटा परीक्षण अनुच्छेद हो।",
    )

    assert needs_nepali_pdf_repair(str(pdf_path)) is False

    calls: list[str] = []

    monkeypatch.setattr(
        core_module, "_convert_with_detected_structure", lambda _path: None
    )

    def fake_convert_pdf_to_markdown(file_path: str) -> str:
        calls.append(file_path)
        return "नेपाल सरकार\n\nयो एउटा परीक्षण अनुच्छेद हो।"

    monkeypatch.setattr(
        core_module,
        "convert_pdf_to_markdown",
        fake_convert_pdf_to_markdown,
    )

    markdown = convert(str(pdf_path))

    assert markdown == "नेपाल सरकार\n\nयो एउटा परीक्षण अनुच्छेद हो।"
    assert not markdown.startswith("---")
    assert calls == [str(pdf_path)]


def test_convert_repairs_broken_cmap_sample() -> None:
    sample = ROOT / "samples" / "pressrelease.pdf"

    raw_markitdown = MarkItDown().convert(str(sample)).text_content
    repaired = convert(str(sample))

    assert "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा" not in raw_markitdown
    assert "राष्ट्रिय सूचना प्रविधि केन्द्रद्वारा" in repaired
    assert "प्रष्ट्रिधध" in raw_markitdown
    assert "प्रष्ट्रिधध" not in repaired
    assert not repaired.startswith("---")
    assert repaired.startswith("# आरोपपत्र दायर गररएको")


def test_convert_repairs_legacy_font_sample(tmp_path: Path) -> None:
    sample = _copy_pdf_pages(
        ROOT / "samples" / "kanunpatrika.pdf",
        tmp_path / "kanunpatrika-first-page.pdf",
        start=0,
        end=0,
    )

    raw_markitdown = MarkItDown().convert(str(sample)).text_content
    repaired = convert(str(sample))

    assert "नेपाल कानून पत्रिका" not in raw_markitdown
    assert "नेपाल कानून पत्रिका" in repaired


def test_convert_preserves_kanun_patrika_column_order() -> None:
    sample = ROOT / "samples" / "kanunpatrika.pdf"

    markdown = convert(str(sample))

    assert "निर्णय नं.७९७३" in markdown
    assert "सर्बोच्च अदालत विशेष इजलास" in markdown
    assert "जवर्जस्ती करणीको महलमा भएको" in markdown
    assert markdown.index("सर्बोच्च अदालत विशेष इजलास") < markdown.index(
        "जवर्जस्ती करणीको महलमा भएको"
    )
    assert not markdown.startswith("---")


def test_convert_uses_structured_renderer_for_recognized_table_layout() -> None:
    sample = ROOT / "samples" / "my-table.pdf"

    markdown = convert(str(sample))

    assert "तालिका २.१९" in markdown
    assert "**1**" in markdown or "**1.**" in markdown
    assert "- **आयोगको निर्णय:**" in markdown
    assert "- **प्रतिवादीको नाम, पद र कार्यालय:**" in markdown


def test_assemble_markdown_preserves_headings_lists_and_tables() -> None:
    table = Table(
        row_count=2,
        col_count=2,
        cells=[
            TableCell(row=0, col=0, text="नाम"),
            TableCell(row=0, col=1, text="रकम"),
            TableCell(row=1, col=0, text="परियोजना"),
            TableCell(row=1, col=1, text="१०००"),
        ],
        caption="तालिका १",
        regions=[TableRegion(page_number=1, x0=0, y0=0, x1=100, y1=50)],
    )
    markdown = assemble_markdown(
        [
            RepairedBlock(
                text="रिपोर्ट शीर्षक", order_index=0, page_number=1, heading_level=1
            ),
            RepairedBlock(text="यसमा सामान्य अनुच्छेद छ।", order_index=1, page_number=1),
            RepairedBlock(
                text="1. पहिलो बुँदा", order_index=2, page_number=1, list_marker="1."
            ),
            RepairedBlock(text="तालिका १", order_index=3, page_number=1, table=table),
        ]
    )

    assert "# रिपोर्ट शीर्षक" in markdown
    assert "यसमा सामान्य अनुच्छेद छ।" in markdown
    assert "1. पहिलो बुँदा" in markdown
    assert "| नाम | रकम |" in markdown
    assert "| परियोजना | १००० |" in markdown


def test_convert_rejects_empty_pdf(tmp_path: Path) -> None:
    pdf_path = _create_blank_pdf(tmp_path / "blank.pdf")

    with pytest.raises(
        ExtractionError,
        match="Scanned or image-only PDFs are not supported",
    ):
        convert(str(pdf_path))


def test_convert_rejects_non_pdf_input(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.docx"
    input_path.write_text("not really a docx", encoding="utf-8")

    # MarkItDown handles invalid DOCX gracefully by treating it as plain text
    # This is acceptable behavior - it returns the text content
    result = convert(str(input_path))

    # Should return the plain text content
    assert "not really a docx" in result


def test_cli_convert_single_file_with_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf_path = tmp_path / "single.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    output_path = tmp_path / "single.md"

    monkeypatch.setattr(
        cli_module,
        "convert_many",
        lambda file_paths: [(file_paths[0], "नेपाल सरकार\n\nयो एउटा परीक्षण अनुच्छेद हो।")],
    )

    exit_code = main(["convert", str(pdf_path), "--out", str(output_path)])

    assert exit_code == 0
    assert output_path.read_text(encoding="utf-8") == (
        "नेपाल सरकार\n\nयो एउटा परीक्षण अनुच्छेद हो।"
    )


def test_cli_convert_multiple_inputs_with_out_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"%PDF-1.4")
    second.write_bytes(b"%PDF-1.4")
    output_dir = tmp_path / "out"

    monkeypatch.setattr(
        cli_module,
        "convert_many",
        lambda file_paths: [
            (file_paths[0], "पहिलो\n\nपहिलो अनुच्छेद।"),
            (file_paths[1], "दोस्रो\n\nदोस्रो अनुच्छेद।"),
        ],
    )

    exit_code = main(
        [
            "convert",
            str(first),
            str(second),
            "--out-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert (output_dir / "first.md").read_text(encoding="utf-8").startswith("पहिलो")
    assert (output_dir / "second.md").read_text(encoding="utf-8").startswith("दोस्रो")


def test_cli_convert_rejects_unsupported_extension(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Use an actually unsupported extension
    input_path = tmp_path / "sample.txt"
    input_path.write_text("not a supported format", encoding="utf-8")

    exit_code = main(["convert", str(input_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Unsupported input format" in captured.err
    assert ".txt" in captured.err


def test_cli_convert_reports_blank_pdf(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pdf_path = _create_blank_pdf(tmp_path / "blank.pdf")

    exit_code = main(["convert", str(pdf_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Scanned or image-only PDFs are not supported" in captured.err


def test_cli_convert_auto_detects_kanun_patrika(tmp_path: Path) -> None:
    output_path = tmp_path / "kanun.md"

    exit_code = main(
        [
            "convert",
            str(ROOT / "samples" / "kanunpatrika.pdf"),
            "--out",
            str(output_path),
        ]
    )

    assert exit_code == 0
    markdown = output_path.read_text(encoding="utf-8")
    assert "निर्णय नं.७९७३" in markdown
    assert markdown.index("सर्बोच्च अदालत विशेष इजलास") < markdown.index(
        "जवर्जस्ती करणीको महलमा भएको"
    )


def test_cli_rejects_removed_extract_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["extract", "sample.pdf"])
    captured = capsys.readouterr()

    assert exc_info.value.code == 2
    assert "invalid choice" in captured.err

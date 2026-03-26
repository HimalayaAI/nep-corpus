"""Integration tests for end-to-end conversion success."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from likhit.cli import main
from likhit.core import convert

from .conftest import (
    TEST_DATA_DIR,
    assert_fixture_size_under_threshold,
    compute_total_fixture_size,
    discover_all_fixtures,
    discover_fixtures_by_extension,
)


# Platform detection for DOC tests
IS_WINDOWS = platform.system() == "Windows"


def _has_working_doc_runtime() -> bool:
    """Return True when at least one DOC extractor runtime is available."""
    # macOS ships textutil, which we support as a DOC fallback extractor.
    if shutil.which("textutil"):
        return True

    antiword_bin = shutil.which("antiword")
    if antiword_bin:
        return True

    try:
        import pyantiword

        bundled = Path(pyantiword.__file__).resolve().parent / "antiword"
        if not bundled.exists():
            return False
        subprocess.run(
            [str(bundled), "-h"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return True
    except (OSError, ImportError):
        return False


DOC_EXTRACTION_AVAILABLE = (not IS_WINDOWS) and _has_working_doc_runtime()
SKIP_DOC_WHEN_UNAVAILABLE = pytest.mark.skipif(
    not DOC_EXTRACTION_AVAILABLE,
    reason=(
        "DOC extraction requires a working runtime (antiword or macOS textutil). "
        "On macOS you can also install antiword with: brew install antiword"
    ),
)


class TestFixtureGovernance:
    """Tests to ensure fixture directory meets requirements."""

    def test_fixture_directory_exists(self) -> None:
        """Verify test_data directory exists."""
        assert TEST_DATA_DIR.exists(), f"Test data directory not found: {TEST_DATA_DIR}"
        assert (
            TEST_DATA_DIR.is_dir()
        ), f"Test data path is not a directory: {TEST_DATA_DIR}"

    def test_fixture_size_under_threshold(self) -> None:
        """Verify total fixture size is under 50 MB."""
        assert_fixture_size_under_threshold(threshold_mb=50)

        # Also log the actual size for visibility
        total_bytes = compute_total_fixture_size()
        total_mb = total_bytes / (1024 * 1024)
        print(f"\nTotal fixture size: {total_mb:.2f} MB")

    def test_required_formats_present(self) -> None:
        """Verify at least one fixture of each required format exists."""
        pdf_fixtures = discover_fixtures_by_extension(".pdf")
        docx_fixtures = discover_fixtures_by_extension(".docx")
        doc_fixtures = discover_fixtures_by_extension(".doc")

        assert len(pdf_fixtures) > 0, "No PDF fixtures found"
        assert len(docx_fixtures) > 0, "No DOCX fixtures found"
        assert len(doc_fixtures) > 0, "No DOC fixtures found"

        print(
            f"\nFound {len(pdf_fixtures)} PDF, {len(docx_fixtures)} DOCX, {len(doc_fixtures)} DOC fixtures"
        )


class TestCoreAPIConversion:
    """Integration tests for likhit.core.convert() API."""

    @pytest.mark.parametrize("fixture_path", discover_all_fixtures())
    def test_convert_produces_nonempty_output(self, fixture_path: Path) -> None:
        """Test that convert() produces non-empty output for all fixtures."""
        # Skip DOC files when antiword runtime is unavailable on this host
        if fixture_path.suffix.lower() == ".doc" and not DOC_EXTRACTION_AVAILABLE:
            pytest.skip(
                "DOC extraction requires a working runtime (antiword or macOS textutil)"
            )

        markdown = convert(str(fixture_path))

        assert markdown, f"Empty output for {fixture_path.name}"
        assert len(markdown) > 0, f"Zero-length output for {fixture_path.name}"
        assert isinstance(
            markdown, str
        ), f"Output is not a string for {fixture_path.name}"

    def test_ciaa_pdf_contains_expected_markers(self) -> None:
        """Test that CIAA press release PDF contains expected Nepali markers."""
        ciaa_pdf = TEST_DATA_DIR / "ciaa_pressrelease_sample.pdf"
        if not ciaa_pdf.exists():
            pytest.skip("CIAA press release sample not found")

        markdown = convert(str(ciaa_pdf))

        # Check for CIAA-specific markers
        assert any(
            marker in markdown for marker in ["आरोपपत्र", "अख्तियार", "प्रेस"]
        ), "CIAA markers not found in output"

    def test_kanun_patrika_pdf_contains_expected_markers(self) -> None:
        """Test that Kanun Patrika PDF contains expected markers."""
        kanun_pdf = TEST_DATA_DIR / "kanun_patrika_sample.pdf"
        if not kanun_pdf.exists():
            pytest.skip("Kanun Patrika sample not found")

        markdown = convert(str(kanun_pdf))

        # Check for Kanun Patrika-specific markers
        assert any(
            marker in markdown for marker in ["निर्णय नं", "कानून पत्रिका"]
        ), "Kanun Patrika markers not found in output"

    def test_ciaa_docx_contains_expected_markers(self) -> None:
        """Test that CIAA DOCX contains expected markers."""
        ciaa_docx = TEST_DATA_DIR / "ciaa_pressrelease_sample.docx"
        if not ciaa_docx.exists():
            pytest.skip("CIAA DOCX sample not found")

        markdown = convert(str(ciaa_docx))

        # Check for CIAA-specific markers
        assert any(
            marker in markdown for marker in ["अख्तियार", "प्रेस", "विषय"]
        ), "CIAA markers not found in DOCX output"

    @SKIP_DOC_WHEN_UNAVAILABLE
    def test_ciaa_doc_contains_expected_markers(self) -> None:
        """Test that CIAA DOC contains expected markers."""
        ciaa_doc = TEST_DATA_DIR / "ciaa_legacy_sample.doc"
        if not ciaa_doc.exists():
            pytest.skip("CIAA DOC sample not found")

        markdown = convert(str(ciaa_doc))

        # Check for CIAA-specific markers
        assert any(
            marker in markdown for marker in ["अख्तियार", "प्रेस", "विषय"]
        ), "CIAA markers not found in DOC output"


class TestCLIConversion:
    """Integration tests for CLI conversion workflow."""

    def test_cli_convert_single_pdf(self, tmp_path: Path) -> None:
        """Test CLI conversion of a single PDF file."""
        ciaa_pdf = TEST_DATA_DIR / "ciaa_pressrelease_sample.pdf"
        if not ciaa_pdf.exists():
            pytest.skip("CIAA press release sample not found")

        output_file = tmp_path / "output.md"

        exit_code = main(["convert", str(ciaa_pdf), "--out", str(output_file)])

        assert exit_code == 0, "CLI command failed"
        assert output_file.exists(), "Output file not created"

        content = output_file.read_text(encoding="utf-8")
        assert len(content) > 0, "Output file is empty"
        assert "अख्तियार" in content or "प्रेस" in content, "Expected markers not found"

    def test_cli_convert_multiple_files_with_out_dir(self, tmp_path: Path) -> None:
        """Test CLI batch conversion with --out-dir."""
        pdf_fixture = TEST_DATA_DIR / "ciaa_pressrelease_sample.pdf"
        docx_fixture = TEST_DATA_DIR / "ciaa_pressrelease_sample.docx"

        if not pdf_fixture.exists() or not docx_fixture.exists():
            pytest.skip("Required fixtures not found")

        output_dir = tmp_path / "output"

        # Include DOC only when a working DOC runtime is available
        if not DOC_EXTRACTION_AVAILABLE:
            exit_code = main(
                [
                    "convert",
                    str(pdf_fixture),
                    str(docx_fixture),
                    "--out-dir",
                    str(output_dir),
                ]
            )
        else:
            doc_fixture = TEST_DATA_DIR / "ciaa_legacy_sample.doc"
            exit_code = main(
                [
                    "convert",
                    str(pdf_fixture),
                    str(docx_fixture),
                    str(doc_fixture),
                    "--out-dir",
                    str(output_dir),
                ]
            )

        assert exit_code == 0, "CLI batch command failed"
        assert output_dir.exists(), "Output directory not created"

        # Check that output files were created
        output_files = list(output_dir.glob("*.md"))
        expected_count = 2 if not DOC_EXTRACTION_AVAILABLE else 3
        assert (
            len(output_files) == expected_count
        ), f"Expected {expected_count} output files, found {len(output_files)}"

        # Verify all output files are non-empty
        for output_file in output_files:
            content = output_file.read_text(encoding="utf-8")
            assert len(content) > 0, f"Output file {output_file.name} is empty"

    def test_cli_convert_mixed_formats_batch(self, tmp_path: Path) -> None:
        """Test CLI batch conversion with mixed PDF, DOCX, DOC formats."""
        fixtures = discover_all_fixtures()

        if len(fixtures) < 2:
            pytest.skip("Not enough fixtures for batch test")

        # Filter out DOC when a working DOC runtime is unavailable
        if not DOC_EXTRACTION_AVAILABLE:
            fixtures = [f for f in fixtures if f.suffix.lower() != ".doc"]

        output_dir = tmp_path / "batch_output"

        args = ["convert"] + [str(f) for f in fixtures] + ["--out-dir", str(output_dir)]
        exit_code = main(args)

        assert exit_code == 0, "CLI batch command failed"
        assert output_dir.exists(), "Output directory not created"

        # Check that output files were created for each input
        output_files = list(output_dir.glob("*.md"))
        assert len(output_files) == len(
            fixtures
        ), f"Expected {len(fixtures)} output files, found {len(output_files)}"

        # Verify all output files are non-empty
        for output_file in output_files:
            content = output_file.read_text(encoding="utf-8")
            assert len(content) > 0, f"Output file {output_file.name} is empty"

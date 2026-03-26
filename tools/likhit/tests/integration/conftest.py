"""Shared fixtures and helpers for integration tests."""

from __future__ import annotations

from pathlib import Path


# Test data directory
TEST_DATA_DIR = Path(__file__).parent / "test_data"


def discover_fixtures_by_extension(extension: str) -> list[Path]:
    """Discover fixture files by extension.

    Args:
        extension: File extension to filter by (e.g., '.pdf', '.docx', '.doc')

    Returns:
        Sorted list of fixture file paths
    """
    if not extension.startswith("."):
        extension = f".{extension}"

    fixtures = sorted(TEST_DATA_DIR.glob(f"*{extension}"))
    return fixtures


def discover_all_fixtures() -> list[Path]:
    """Discover all fixture files.

    Returns:
        Sorted list of all fixture file paths
    """
    fixtures = sorted(TEST_DATA_DIR.glob("*.*"))
    # Filter out non-document files
    valid_extensions = {".pdf", ".docx", ".doc"}
    fixtures = [f for f in fixtures if f.suffix.lower() in valid_extensions]
    return fixtures


def compute_total_fixture_size() -> int:
    """Compute total size of all fixtures in bytes.

    Returns:
        Total size in bytes
    """
    fixtures = discover_all_fixtures()
    total_size = sum(f.stat().st_size for f in fixtures)
    return total_size


def assert_fixture_size_under_threshold(threshold_mb: int = 50) -> None:
    """Assert that total fixture size is under threshold.

    Args:
        threshold_mb: Maximum allowed size in megabytes

    Raises:
        AssertionError: If total size exceeds threshold
    """
    total_bytes = compute_total_fixture_size()
    total_mb = total_bytes / (1024 * 1024)
    threshold_bytes = threshold_mb * 1024 * 1024

    assert total_bytes < threshold_bytes, (
        f"Fixture size {total_mb:.2f} MB exceeds {threshold_mb} MB threshold. "
        f"Please reduce fixture sizes or remove unnecessary files."
    )

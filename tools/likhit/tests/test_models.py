from __future__ import annotations

import pytest

from likhit.errors import ValidationError
from likhit.models import DocumentType, ExtractionResult, Section


def test_document_type_parse_accepts_ciaa_press_release() -> None:
    assert DocumentType.parse("ciaa-press-release") is DocumentType.CIAA_PRESS_RELEASE


def test_document_type_parse_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError):
        DocumentType.parse("unknown")


def test_extraction_result_requires_sections() -> None:
    with pytest.raises(ValidationError):
        ExtractionResult(
            title="x", doc_type=DocumentType.CIAA_PRESS_RELEASE, sections=[]
        )


def test_section_rejects_empty_body() -> None:
    with pytest.raises(ValidationError):
        Section(heading="Heading", body="   ")


def test_section_rejects_non_string_body() -> None:
    with pytest.raises(ValidationError, match="Section body must be a string"):
        Section(heading="Heading", body=None)  # type: ignore[arg-type]


def test_section_rejects_non_string_heading() -> None:
    with pytest.raises(
        ValidationError, match="Section heading must be a string or None"
    ):
        Section(heading=1, body="Body")  # type: ignore[arg-type]


def test_extraction_result_rejects_non_string_title() -> None:
    with pytest.raises(
        ValidationError, match="ExtractionResult title must be a string"
    ):
        ExtractionResult(
            title=1,  # type: ignore[arg-type]
            doc_type=DocumentType.CIAA_PRESS_RELEASE,
            sections=[Section(heading=None, body="Body")],
        )

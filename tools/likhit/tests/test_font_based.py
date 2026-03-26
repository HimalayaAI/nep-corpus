from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from likhit.errors import ExtractionError, ValidationError
from likhit.extractors.base import RawDocument, TextFragment
from likhit.extractors.font_classifier import classify_font
import likhit.extractors.font_based as font_based_module
from likhit.extractors.font_based import (
    FontBasedStrategy,
    join_spans_with_layout,
    join_words_with_spacing,
    normalize_extracted_word,
    parse_page_range,
)
from likhit.extractors.kalimati import _get_font_correction_map
from likhit.handlers.ciaa_press_release import CIAAPressReleaseHandler


ROOT = Path(__file__).resolve().parents[1]


def _sample_path(*candidates: str) -> Path:
    for candidate in candidates:
        path = ROOT / "samples" / candidate
        if path.exists():
            return path
    raise FileNotFoundError(
        f"Missing sample PDF in {ROOT / 'samples'}. Tried: {', '.join(candidates)}"
    )


PRESS_RELEASE = _sample_path("pressrelease.pdf")


def test_parse_page_range_accepts_single_page() -> None:
    assert parse_page_range("2", 5) == (1, 1)


def test_parse_page_range_accepts_ranges() -> None:
    assert parse_page_range("2-4", 5) == (1, 3)


@pytest.mark.parametrize("spec", ["0", "4-2", "abc", "1-", "-2"])
def test_parse_page_range_rejects_invalid_values(spec: str) -> None:
    with pytest.raises(ValidationError, match="Invalid page range format"):
        parse_page_range(spec, 5)


def test_parse_page_range_clamps_end_to_document_length() -> None:
    assert parse_page_range("3-9", 5) == (2, 4)


def test_parse_page_range_rejects_start_beyond_document_length() -> None:
    with pytest.raises(ValidationError, match="starts beyond document length"):
        parse_page_range("6-8", 5)


def test_classify_font_detects_expected_strategies() -> None:
    assert classify_font("ABCDEF+Preeti", "Type0") == "legacy_remap"
    assert classify_font("ABCDEF+Kalimati", "Type0") == "broken_cmap"
    assert classify_font("Helvetica", "Type1") == "correct"


def test_font_based_strategy_rejects_non_pdf_input(tmp_path: Path) -> None:
    source = tmp_path / "document.docx"
    source.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(ValidationError, match="Please upload a PDF file"):
        FontBasedStrategy().extract_text(str(source))


def test_font_based_strategy_auto_detects_and_converts_legacy_fonts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "legacy.pdf"
    source.write_bytes(b"%PDF-1.4")

    class FakePage:
        def get_fonts(self, full: bool = True) -> list[tuple[object, ...]]:
            del full
            return [(1, "ttf", "Type0", "ABCDEF+Preeti", "Identity-H")]

        def get_text(self, mode: str, flags: int | None = None) -> dict[str, object]:
            assert mode == "dict"
            del flags
            return {
                "blocks": [
                    {
                        "lines": [
                            {
                                "spans": [
                                    {
                                        "font": "ABCDEF+Preeti",
                                        "text": "abc",
                                        "bbox": (10.0, 20.0, 40.0, 35.0),
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }

    class FakeDoc:
        page_count = 1

        def __getitem__(self, index: int) -> FakePage:
            assert index == 0
            return FakePage()

        def close(self) -> None:
            return None

    monkeypatch.setattr(font_based_module.fitz, "open", lambda _: FakeDoc())
    monkeypatch.setattr(
        font_based_module,
        "get_converter",
        lambda _font_name: (lambda text: f"converted:{text}"),
    )

    result = FontBasedStrategy().extract_text(str(source))

    assert result.raw_text == "converted:abc"
    assert result.fragments[0].text == "converted:abc"


def test_font_based_strategy_wraps_unexpected_extraction_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_fix_kalimati_cmap(doc: object) -> tuple[object, bool]:
        raise RuntimeError("boom")

    monkeypatch.setattr(font_based_module, "fix_kalimati_cmap", fake_fix_kalimati_cmap)

    with pytest.raises(ExtractionError, match="Failed to extract text from PDF"):
        FontBasedStrategy().extract_text(str(PRESS_RELEASE))


def test_kalimati_fix_requires_fonttools(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("fontTools"):
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ExtractionError, match="fonttools is required"):
        _get_font_correction_map(None, 1)  # type: ignore[arg-type]


def test_handler_keeps_table_content_after_numbered_prose() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: परीक्षण शीर्षक ।", 1, 180, 130, 340, 150),
            TextFragment("1. पहिलो बुँदा", 1, 45, 200, 400, 220),
            TextFragment("यसको व्याख्या", 1, 45, 220, 420, 240),
            TextFragment("देहाय:", 1, 250, 260, 320, 280),
            TextFragment("सि.नं स्तम्भ", 1, 45, 280, 420, 300),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert "1. पहिलो बुँदा यसको व्याख्या" in result.sections[0].body
    assert "देहाय: सि.नं स्तम्भ" in result.sections[0].body


def test_handler_keeps_footer_signature_in_body() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: परीक्षण शीर्षक ।", 1, 180, 130, 340, 150),
            TextFragment("मुख्य अनुच्छेद", 1, 45, 200, 420, 220),
            TextFragment("हस्ताक्षरकर्ता", 1, 300, 500, 390, 520),
            TextFragment("कुनै व्यक्ति", 1, 260, 520, 420, 540),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert "मिति: २०८२।०१।१४" in result.sections[0].body
    assert "विषय: परीक्षण शीर्षक" in result.sections[0].body
    assert "मुख्य अनुच्छेद" in result.sections[0].body
    assert "हस्ताक्षरकर्ता" in result.sections[0].body
    assert "कुनै व्यक्ति" in result.sections[0].body


def test_handler_keeps_body_when_it_starts_with_table_content() -> None:
    handler = CIAAPressReleaseHandler()
    raw_document = RawDocument(
        paragraphs=[],
        raw_text="",
        fragments=[
            TextFragment("मिति: २०८२।०१।१४", 1, 200, 100, 300, 120),
            TextFragment("विषय: परीक्षण शीर्षक ।", 1, 180, 130, 340, 150),
            TextFragment("देहाय:", 1, 250, 200, 320, 220),
            TextFragment("सि.नं", 1, 45, 220, 120, 240),
        ],
    )

    result = handler.build_result(raw_document, {})

    assert "मिति: २०८२।०१।१४" in result.sections[0].body
    assert "विषय: परीक्षण शीर्षक" in result.sections[0].body
    assert "देहाय: सि.नं" in result.sections[0].body


def test_join_words_with_spacing_preserves_word_boundary() -> None:
    joined = join_words_with_spacing(["Mindray", "BS-230"])

    assert joined == "Mindray BS-230"


def test_join_spans_with_layout_keeps_font_split_word_together() -> None:
    joined = join_spans_with_layout(
        [
            (10.0, 0.0, 20.0, 10.0, "२०७४"),
            (19.95, 0.0, 22.0, 10.0, "/"),
            (21.95, 0.0, 40.0, 10.0, "७५"),
        ]
    )

    assert joined == "२०७४/७५"


def test_join_spans_with_layout_adds_space_for_real_visual_gap() -> None:
    joined = join_spans_with_layout(
        [
            (10.0, 0.0, 30.0, 10.0, "Mindray"),
            (32.0, 0.0, 50.0, 10.0, "BS-230"),
        ]
    )

    assert joined == "Mindray BS-230"


def test_normalize_extracted_word_keeps_spaces_between_kalimati_words() -> None:
    line = join_words_with_spacing(
        [
            normalize_extracted_word("कम\uf000चारीको"),
            normalize_extracted_word("\uf001सफा\uf001रसमा"),
        ]
    )

    assert line == "कर्मचारीको सिफारिसमा"


def test_normalize_extracted_word_keeps_space_before_prebase_marker_word() -> None:
    line = join_words_with_spacing(
        [
            normalize_extracted_word("सञ्चालक"),
            normalize_extracted_word("\uf001वशाल"),
        ]
    )

    assert line == "सञ्चालक विशाल"

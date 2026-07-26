from __future__ import annotations

import json
from pathlib import Path

import pytest
from datasets import Dataset

import scripts.merge_datasets.merge_corpus_to_hf as compiler
from nepali_corpus.dataset_compiler.adapters import (
    AdapterContext,
    get_adapter,
    infer_adapter_name,
)
from scripts.merge_datasets.merge_corpus_to_hf import (
    SourceConfig,
    load_inventory_sources,
    parse_sources,
    resolve_run_adapter,
)


def context(adapter: str, fields: dict) -> AdapterContext:
    return AdapterContext(
        source_name=f"test-{adapter}",
        source_repo="example/dataset",
        source_config="ne",
        source_split="train",
        fields=fields,
        filter_spec=None,
        default_language="ne",
        license="cc-by-4.0",
        task_type=adapter,
    )


def as_dataset(adapter_name: str, row: dict) -> Dataset:
    adapter = get_adapter(adapter_name)
    return Dataset.from_dict(
        adapter.data_dict([row]),
        features=adapter.features(),
    )


def test_text_adapter_preserves_legacy_output_schema() -> None:
    adapter = get_adapter("text")
    mapped = adapter.map_item(
        {
            "body": "  नेपाली पाठ यहाँ छ।  ",
            "link": "https://example.org/1",
            "row_id": 42,
        },
        context(
            "text",
            {"text": "body", "url": "link", "doc_id": "row_id"},
        ),
    )

    assert mapped is not None
    assert mapped.row == {
        "text": "नेपाली पाठ यहाँ छ।",
        "source": "test-text",
        "url": "https://example.org/1",
        "language": "ne",
        "doc_id": "42",
    }
    assert list(adapter.features()) == [
        "text",
        "source",
        "url",
        "language",
        "doc_id",
    ]


def test_asr_adapter_preserves_audio_and_transcription() -> None:
    adapter = get_adapter("asr")
    mapped = adapter.map_item(
        {
            "audio": {"bytes": b"RIFF-audio-one", "path": None},
            "sentence": "नेपालको राजधानी काठमाडौं हो।",
            "speaker": "speaker-7",
            "duration": 1250,
            "sampling_rate": 16000,
            "id": "utterance-1",
        },
        context(
            "asr",
            {
                "audio": "audio",
                "text": "sentence",
                "speaker_id": "speaker",
                "duration_ms": "duration",
                "sampling_rate": "sampling_rate",
                "doc_id": "id",
            },
        ),
    )

    assert mapped is not None
    assert mapped.row["audio"]["bytes"] == b"RIFF-audio-one"
    assert mapped.row["audio_source"] is None
    assert mapped.row["transcription"] == "नेपालको राजधानी काठमाडौं हो।"
    assert mapped.row["normalized_transcription"] == mapped.row["transcription"]
    assert mapped.row["speaker_id"] == "speaker-7"
    assert mapped.row["duration_ms"] == 1250
    assert mapped.row["source_repo"] == "example/dataset"
    assert as_dataset("asr", mapped.row).num_rows == 1

    second = adapter.map_item(
        {
            "audio": {"bytes": b"RIFF-audio-two", "path": None},
            "sentence": "नेपालको राजधानी काठमाडौं हो।",
        },
        context("asr", {"audio": "audio", "text": "sentence"}),
    )
    assert second is not None
    assert mapped.dedupe_key != second.dedupe_key


def test_asr_adapter_materializes_local_audio_path(tmp_path: Path) -> None:
    audio_path = tmp_path / "clip.wav"
    audio_path.write_bytes(b"RIFF-local-audio")
    adapter = get_adapter("asr")

    mapped = adapter.map_item(
        {
            "audio_path": str(audio_path),
            "sentence": "यो स्थानीय अडियो परीक्षण हो।",
        },
        context("asr", {"audio": "audio_path", "text": "sentence"}),
    )

    assert mapped is not None
    assert mapped.row["audio"]["bytes"] == b"RIFF-local-audio"
    assert mapped.row["audio"]["path"] == str(audio_path)
    assert mapped.row["audio_source"] == str(audio_path)
    assert as_dataset("asr", mapped.row).num_rows == 1


def test_asr_adapter_rejects_media_over_size_limit(tmp_path: Path) -> None:
    audio_path = tmp_path / "large.wav"
    audio_path.write_bytes(b"x" * 10)
    adapter = get_adapter("asr")
    limited_context = AdapterContext(
        source_name="limited-asr",
        fields={"audio": "audio", "text": "text"},
        filter_spec=None,
        default_language="ne",
        max_media_bytes=5,
    )

    mapped = adapter.map_item(
        {"audio": str(audio_path), "text": "यो परीक्षण वाक्य हो।"},
        limited_context,
    )

    assert mapped is None


def test_ocr_adapter_preserves_page_image_and_layout_metadata() -> None:
    adapter = get_adapter("ocr")
    mapped = adapter.map_item(
        {
            "scan": {"bytes": b"PNG-page", "path": None},
            "page_text": "नेपाल सरकारको सूचना।",
            "document": "gazette-9",
            "page": 3,
            "boxes": [[10, 20, 30, 40]],
        },
        context(
            "ocr",
            {
                "image": "scan",
                "text": "page_text",
                "document_id": "document",
                "page_number": "page",
                "bounding_boxes": "boxes",
            },
        ),
    )

    assert mapped is not None
    assert mapped.row["image_source"] is None
    assert mapped.row["document_id"] == "gazette-9"
    assert mapped.row["page_number"] == 3
    assert json.loads(mapped.row["bounding_boxes_json"]) == [[10, 20, 30, 40]]
    assert as_dataset("ocr", mapped.row).num_rows == 1


def test_sft_adapter_accepts_sharegpt_and_instruction_pairs() -> None:
    adapter = get_adapter("sft")
    sharegpt = adapter.map_item(
        {
            "conversations": [
                {"from": "human", "value": "नेपाल कहाँ पर्छ?"},
                {"from": "gpt", "value": "नेपाल दक्षिण एसियामा पर्छ।"},
            ]
        },
        context("sft", {"conversations": "conversations"}),
    )
    pair = adapter.map_item(
        {
            "prompt": "नेपालको राजधानी के हो?",
            "answer": "काठमाडौं।",
        },
        context("sft", {"instruction": "prompt", "response": "answer"}),
    )

    assert sharegpt is not None
    assert sharegpt.row["messages"][0]["role"] == "user"
    assert sharegpt.row["messages"][1]["role"] == "assistant"
    assert pair is not None
    assert pair.row["messages"][1]["content"] == "काठमाडौं।"
    assert as_dataset("sft", sharegpt.row).num_rows == 1


def test_inventory_infers_asr_adapter_and_requires_audio(tmp_path: Path) -> None:
    inventory = tmp_path / "asr.jsonl"
    entries = [
        {
            "repo_id": "example/valid-asr",
            "config": "ne",
            "split": "train",
            "mapping_suggested": {"text": "sentence", "audio": "audio"},
            "usable": True,
            "modality": "audio_text",
            "task_type": "asr_transcript",
            "language": "ne",
            "license": "cc-by-4.0",
        },
        {
            "repo_id": "example/no-audio",
            "mapping_suggested": {"text": "sentence", "audio": None},
            "usable": True,
            "modality": "audio_text",
            "task_type": "asr_transcript",
        },
    ]
    inventory.write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries),
        encoding="utf-8",
    )

    sources = load_inventory_sources(str(inventory), None, None)

    assert len(sources) == 1
    assert sources[0].adapter == "asr"
    assert sources[0].language == "ne"
    assert sources[0].license == "cc-by-4.0"


def test_parse_sources_defaults_to_text_and_rejects_mixed_run() -> None:
    sources = parse_sources(
        [
            {
                "name": "legacy",
                "kind": "hf",
                "repo": "example/text",
                "fields": {"text": "text"},
            },
            {
                "name": "speech",
                "kind": "hf",
                "repo": "example/asr",
                "adapter": "asr",
                "fields": {"text": "sentence", "audio": "audio"},
            },
        ]
    )

    assert sources[0].adapter == "text"
    assert sources[1].adapter == "asr"
    with pytest.raises(ValueError, match="only one modality schema"):
        resolve_run_adapter(sources)


def test_checkpoint_is_written_only_after_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.jsonl"
    source_path.write_text(
        json.dumps({"text": "यो अपलोड परीक्षणका लागि पर्याप्त नेपाली पाठ हो।"})
        + "\n",
        encoding="utf-8",
    )
    checkpoint_path = tmp_path / "done.txt"
    upload_events = []

    class FakeApi:
        def repo_info(self, *args, **kwargs):
            raise RuntimeError("target does not exist")

        def create_repo(self, *args, **kwargs):
            return None

    def fake_upload(**kwargs):
        assert not checkpoint_path.exists()
        upload_events.append(kwargs["rows"])

    monkeypatch.setattr(compiler, "HfApi", FakeApi)
    monkeypatch.setattr(compiler, "upload_parquet_batch", fake_upload)

    compiler.merge_and_upload(
        sources=[
            SourceConfig(
                name="local-nepali",
                kind="jsonl",
                path=str(source_path),
                fields={"text": "text"},
            )
        ],
        repo_id="example/target",
        token="test-token",
        batch_size=100,
        incremental=False,
        refresh_dedupe=True,
        dedupe_store_path=str(tmp_path / "dedupe.sqlite"),
        max_batches=None,
        global_filter_spec=None,
        legacy_filter_spec=None,
        default_language="ne",
        cleanup_cache=False,
        checkpoint_path=str(checkpoint_path),
        embed_media=False,
        max_media_bytes=None,
        max_batch_bytes=None,
    )

    assert len(upload_events) == 1
    assert checkpoint_path.read_text(encoding="utf-8").strip() == (
        f"jsonl|{source_path}"
    )


def test_failed_upload_does_not_checkpoint_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.jsonl"
    source_path.write_text(
        json.dumps({"text": "यो असफल अपलोड परीक्षणका लागि नेपाली पाठ हो।"})
        + "\n",
        encoding="utf-8",
    )
    checkpoint_path = tmp_path / "done.txt"

    class FakeApi:
        def repo_info(self, *args, **kwargs):
            raise RuntimeError("target does not exist")

        def create_repo(self, *args, **kwargs):
            return None

    def failed_upload(**kwargs):
        raise RuntimeError("simulated upload failure")

    monkeypatch.setattr(compiler, "HfApi", FakeApi)
    monkeypatch.setattr(compiler, "upload_parquet_batch", failed_upload)

    with pytest.raises(RuntimeError, match="simulated upload failure"):
        compiler.merge_and_upload(
            sources=[
                SourceConfig(
                    name="local-nepali",
                    kind="jsonl",
                    path=str(source_path),
                    fields={"text": "text"},
                )
            ],
            repo_id="example/target",
            token="test-token",
            batch_size=100,
            incremental=False,
            refresh_dedupe=True,
            dedupe_store_path=str(tmp_path / "dedupe.sqlite"),
            max_batches=None,
            global_filter_spec=None,
            legacy_filter_spec=None,
            default_language="ne",
            cleanup_cache=False,
            checkpoint_path=str(checkpoint_path),
            embed_media=False,
            max_media_bytes=None,
            max_batch_bytes=None,
        )

    assert not checkpoint_path.exists()


@pytest.mark.parametrize(
    ("modality", "task_type", "expected"),
    [
        ("text_only", None, "text"),
        ("audio_text", "asr_transcript", "asr"),
        ("image_text", "ocr", "ocr"),
        ("conversation", "sft", "sft"),
    ],
)
def test_adapter_inference(
    modality: str,
    task_type: str | None,
    expected: str,
) -> None:
    assert infer_adapter_name(modality=modality, task_type=task_type) == expected

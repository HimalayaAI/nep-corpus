from pathlib import Path

from nepali_corpus.core.services.dashboard.file_tables import (
    infer_columns_from_jsonl,
    list_file_tables,
    make_table_name,
    read_jsonl_page,
    resolve_file_table,
    search_jsonl,
)


def test_file_table_listing_and_resolution(tmp_path: Path):
    data_root = tmp_path / "data" / "raw"
    data_root.mkdir(parents=True)
    sample = data_root / "raw.jsonl"
    sample.write_text('{"text": "नेपाल"}\n{"text": "सरकार"}\n', encoding="utf-8")

    tables = list_file_tables(repo_root=tmp_path)
    expected_rel = "data/raw/raw.jsonl"
    assert make_table_name(expected_rel) in tables

    table_name = make_table_name(expected_rel)
    resolved = resolve_file_table(table_name, repo_root=tmp_path)
    assert resolved == sample.resolve()


def test_file_table_columns_and_paging(tmp_path: Path):
    data_root = tmp_path / "data" / "final"
    data_root.mkdir(parents=True)
    sample = data_root / "training.jsonl"
    sample.write_text('{"id": 1, "text": "नेपाल"}\n{"id": 2, "text": "सरकार"}\n', encoding="utf-8")

    cols = infer_columns_from_jsonl(sample)
    names = {c["name"] for c in cols}
    assert "id" in names
    assert "text" in names

    page, total = read_jsonl_page(sample, page=1, page_size=1)
    assert total == 2
    assert page[0]["id"] == 1


def test_file_search(tmp_path: Path):
    data_root = tmp_path / "data" / "raw"
    data_root.mkdir(parents=True)
    sample = data_root / "raw.jsonl"
    sample.write_text('{"text": "नेपाल"}\n{"text": "सरकार"}\n', encoding="utf-8")

    matches, total = search_jsonl(sample, "नेपाल", page=1, page_size=10)
    assert total == 1
    assert matches[0]["text"] == "नेपाल"

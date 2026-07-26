import json
from types import SimpleNamespace

import pytest

from nepali_corpus.core.services.scrapers import jawafdehi_scraper
from nepali_corpus.core.services.scrapers.control import ScrapeCoordinator


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.fixture
def materials_file(tmp_path):
    materials = [
        {
            "id": "https://jawafdehi.org/material/ag/12345",
            "title": {"ne": "नेपालको कानुनी कागजात", "en": None},
            "extra": {"date": "2024-01-15"},
        },
        {
            "id": "https://jawafdehi.org/material/ag/67890",
            "title": {"ne": None, "en": "Second legal document"},
            "extra": {"date": "2024-01-16"},
        },
    ]
    path = tmp_path / "materials_list.jsonl"
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in materials),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def detail_payload():
    return {
        "datePublished": "2021-07-22",
        "jawafdehi:datePublishedBS": "२०७८-०४-०७",
        "associatedMedia": [
            {
                "contentUrl": "https://s3.jawafdehi.org/case_uploads/document.pdf",
                "encodingFormat": "application/pdf",
                "jawafdehi:linkRole": "RAW",
                "jawafdehi:provenance": {
                    "sha256": "abc123",
                    "captured_at": "2026-07-12T07:38:18+00:00",
                    "content_length": 123456,
                },
            }
        ],
    }


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"title": {"ne": "नेपालको संविधान", "en": None}}, "नेपालको संविधान"),
        (
            {"title": {"ne": "नेपालको संविधान", "en": "Constitution of Nepal"}},
            "नेपालको संविधान",
        ),
        (
            {"title": {"ne": None, "en": "Constitution of Nepal"}},
            "Constitution of Nepal",
        ),
        ({"title": "Constitution of Nepal"}, "Constitution of Nepal"),
        ({"title": ""}, "Legal Document"),
        ({}, "Legal Document"),
    ],
)
def test_normalize_title(item, expected):
    assert jawafdehi_scraper.normalize_title_from_item(item) == expected


def test_normalize_title_truncates_long_values():
    title = jawafdehi_scraper.normalize_title_from_item({"title": "A" * 250})
    assert len(title) == 200
    assert title.endswith("...")


def test_parse_type_and_id():
    assert jawafdehi_scraper.parse_type_and_id(
        "https://jawafdehi.org/material/ag/12345"
    ) == ("ag", "12345")
    assert jawafdehi_scraper.parse_type_and_id(
        "https://jawafdehi.org/some/other/path"
    ) == (None, None)


def test_create_raw_record_uses_pdf_and_live_provenance(
    monkeypatch, materials_file, detail_payload
):
    monkeypatch.setattr(
        jawafdehi_scraper,
        "request_with_retries",
        lambda *args, **kwargs: FakeResponse(detail_payload),
    )

    records = jawafdehi_scraper.create_raw_records_from_materials(
        str(materials_file), max_items=1
    )

    assert len(records) == 1
    record = records[0]
    assert record.url.endswith("document.pdf")
    assert record.content_type == "pdf"
    assert record.title == "नेपालको कानुनी कागजात"
    assert record.language == "ne"
    assert record.published_at == "2021-07-22"
    assert record.date_bs == "२०७८-०४-०७"
    assert record.raw_meta["material_url"].endswith("/material/ag/12345")
    assert record.raw_meta["checksum"] == "abc123"
    assert record.raw_meta["provenance"]["content_length"] == 123456


def test_create_raw_records_enforces_max_items(
    monkeypatch, materials_file, detail_payload
):
    calls = []

    def fake_request(*args, **kwargs):
        calls.append(args[1])
        return FakeResponse(detail_payload)

    monkeypatch.setattr(jawafdehi_scraper, "request_with_retries", fake_request)

    records = jawafdehi_scraper.create_raw_records_from_materials(
        str(materials_file), max_items=1
    )

    assert len(records) == 1
    assert len(calls) == 1
    assert calls[0].endswith("/api/materials/ag/12345")


def test_create_raw_records_zero_limit_avoids_requests(
    monkeypatch, materials_file
):
    def unexpected_request(*args, **kwargs):
        raise AssertionError("No API request expected for max_items=0")

    monkeypatch.setattr(
        jawafdehi_scraper, "request_with_retries", unexpected_request
    )
    assert (
        jawafdehi_scraper.create_raw_records_from_materials(
            str(materials_file), max_items=0
        )
        == []
    )


def test_download_limit_is_applied_after_resume_filter(
    monkeypatch, tmp_path, materials_file
):
    batch_dir = tmp_path / "batch"
    monkeypatch.setattr(jawafdehi_scraper, "BATCH_DIR", str(batch_dir))
    monkeypatch.setattr(
        jawafdehi_scraper, "MANIFEST_FILE", str(tmp_path / "manifest.jsonl")
    )
    monkeypatch.setattr(
        jawafdehi_scraper,
        "FOLDER_STATE_FILE",
        str(tmp_path / "folder_state.json"),
    )

    downloader = jawafdehi_scraper.JawafdehiDownloader(download_workers=1)
    first_url = "https://jawafdehi.org/material/ag/12345"
    second_url = "https://jawafdehi.org/material/ag/67890"
    downloader.manifest = {first_url: {"status": "uploaded"}}
    downloaded = []

    def fake_download(item):
        downloaded.append(item["id"])
        return {
            "material_url": item["id"],
            "status": "downloaded",
            "files": [],
            "file_count": 1,
        }

    monkeypatch.setattr(downloader, "download_one", fake_download)
    monkeypatch.setattr(downloader, "upload_batch", lambda path: True)

    uploaded = downloader.download_and_upload(
        str(materials_file), max_items=1
    )

    assert uploaded == 1
    assert downloaded == [second_url]


def test_folder_state_is_isolated(monkeypatch, tmp_path):
    state_path = tmp_path / "folder_state.json"
    monkeypatch.setattr(
        jawafdehi_scraper, "FOLDER_STATE_FILE", str(state_path)
    )
    downloader = jawafdehi_scraper.JawafdehiDownloader()
    state = {"folder_num": 1, "files_in_folder": 8500, "total_files": 8500}

    downloader.save_folder_state(state)

    assert downloader.load_folder_state() == state
    assert json.loads(state_path.read_text()) == state


def test_upload_batch_uses_sharded_destination(monkeypatch):
    command = []

    def fake_run(args, **kwargs):
        command.extend(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(jawafdehi_scraper.subprocess, "run", fake_run)
    downloader = jawafdehi_scraper.JawafdehiDownloader(
        repo_id="himalaya-ai/test", path_in_repo="pdfs"
    )

    assert downloader.upload_batch("pdfs/0003")
    assert command == [
        "hf",
        "upload",
        "himalaya-ai/test",
        jawafdehi_scraper.BATCH_DIR,
        "pdfs/0003",
        "--repo-type=dataset",
    ]


def test_coordinator_job_executes_with_bounded_limit(
    monkeypatch, tmp_path
):
    registry = [
        {
            "id": "jawafdehi_test",
            "name": "JawafDehi Test",
            "url": "https://jawafdehi.org",
            "source_type": "government",
            "category": "jawafdehi_test",
            "scraper_class": "jawafdehi",
        }
    ]
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(
        jawafdehi_scraper, "create_raw_records_from_materials", fake_create
    )
    coordinator = object.__new__(ScrapeCoordinator)
    jobs = coordinator._build_jobs(
        ["Gov"],
        max_pages=3,
        govt_registry_path=str(registry_path),
        govt_registry_groups=["jawafdehi_test"],
    )

    assert len(jobs) == 1
    assert jobs[0].scraper_class == "jawafdehi"
    assert jobs[0].func() == []
    assert calls == [
        {"materials_file": "materials_list.jsonl", "max_items": 3}
    ]


def test_download_safety_defaults():
    assert jawafdehi_scraper.DOWNLOAD_WORKERS == 8
    assert jawafdehi_scraper.MAX_DOWNLOAD_BYTES == 100 * 1024 * 1024

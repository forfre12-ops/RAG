"""Safety and parser tests for the official public-proxy collector."""
from __future__ import annotations

import hashlib
import io
import json
from email.message import Message
from pathlib import Path

import pytest

from scripts import collect_public_proxy_docs as collector


SOURCE_ID = "kma-weather-yearbooks"


def _registry_source(source_id: str = SOURCE_ID, **changes: object) -> dict:
    row = {
        "source_id": source_id,
        "title": "official source",
        "provider": "official provider",
        "page_url": "https://www.data.go.kr/data/15050682/fileData.do",
        "license": "KOGL-0",
        "source_tier": "tier_1_official_primary",
        "status": "eligible",
        "third_party_rights": "cleared",
        "checked_at": "2026-08-08",
        "expected_document_count": 19,
    }
    row.update(changes)
    return row


def _pdf_document(**changes: object) -> dict:
    row = {
        "source_id": SOURCE_ID,
        "document_id": "kma-weather-yearbooks-test",
        "title": "2025년 기상연감",
        "filename": "yearbook_2025.pdf",
        "format": "PDF",
        "declared_size_bytes": None,
        "download_request": {
            "url": "https://www.kma.go.kr/download_01/yearbook_2025.pdf",
            "method": "GET",
        },
    }
    row.update(changes)
    return row


class _FakeResponse(io.BytesIO):
    def __init__(
        self,
        body: bytes,
        *,
        url: str = "https://www.kma.go.kr/download_01/yearbook_2025.pdf",
        content_type: str = "application/pdf",
        content_length: int | None = None,
    ) -> None:
        super().__init__(body)
        self._url = url
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict] = []

    def open_stream(self, url: str, **kwargs: object) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


def test_default_cli_is_discover_only_and_download_is_explicit() -> None:
    default = collector.parse_args([])
    assert not default.download
    assert not default.discover_only
    assert collector.parse_args(["--discover-only"]).discover_only
    assert collector.parse_args(["--download"]).download
    with pytest.raises(SystemExit):
        collector.parse_args(["--discover-only", "--download"])


def test_only_registry_approved_implemented_sources_are_selected() -> None:
    registry = {"sources": [_registry_source()]}
    selected = collector.select_registry_sources(registry, [SOURCE_ID])
    assert [row["source_id"] for row in selected] == [SOURCE_ID]

    blocked = {"sources": [_registry_source(status="blocked", license="KOGL-4")]}
    with pytest.raises(collector.CollectionError, match="not registry-approved"):
        collector.select_registry_sources(blocked, [SOURCE_ID])
    with pytest.raises(collector.CollectionError, match="does not implement"):
        collector.select_registry_sources(registry, ["not-configured"])

    conditional = {
        "sources": [
            _registry_source(
                license="KOGL-1",
                source_tier="tier_2_official_index",
                status="conditional",
                third_party_rights="requires_item_verification",
            )
        ]
    }
    selected = collector.select_registry_sources(conditional, [SOURCE_ID])
    assert selected[0]["status"] == "conditional"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.kma.go.kr/file.pdf",
        "https://evil.example/file.pdf",
        "https://user:pass@www.kma.go.kr/file.pdf",
        "https://www.kma.go.kr:444/file.pdf",
    ],
)
def test_url_allowlist_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(collector.CollectionError):
        collector.validate_https_url(url, {"www.kma.go.kr"})


def test_catalog_license_snapshot_is_verified_but_not_promoted_to_item_rights() -> None:
    raw = json.dumps(
        {
            "name": "기상청_기상연감(연간)",
            "license": "공공저작물 : 출처표시 (제 1유형)",
            "creator": {"name": "기상청"},
            "dateModified": "2025-11-17",
        },
        ensure_ascii=False,
    ).encode()
    verified = collector.verify_license_payload(raw, "KOGL-1")
    assert verified["observed_license_code"] == "KOGL-1"

    evidence = {
        "license_code": "KOGL-1",
        "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "snapshot_path": "evidence/kma/license-001.json",
    }
    document = _pdf_document()
    collector._bind_evidence(document, evidence)
    assert document["eligibility_status"] == "license_hold"
    assert document["download_authorized"] is False
    assert document["license_evidence"]["item_level_evidence"]["status"] == "missing"
    assert document["license_evidence"]["document_binding_sha256"]


def test_catalog_license_mismatch_fails_closed() -> None:
    raw = json.dumps({"license": "공공저작물 : 변경금지 (제 3유형)"}).encode()
    with pytest.raises(collector.CollectionError, match="licence mismatch"):
        collector.verify_license_payload(raw, "KOGL-1")


def test_kma_parser_accepts_only_yearbook_pdf_download_anchors() -> None:
    page = """
    <a class="down" href="/download_01/yearbook_2025.pdf">다운로드</a>
    <a class="down" href="/download_01/yearbook_2005_1.pdf">다운로드</a>
    <a class="down" href="/download_01/Annual_Report_2012.pdf">English</a>
    <a href="/download_01/yearbook_2024.pdf">share</a>
    """
    documents = collector.parse_kma_page(
        page,
        "https://www.kma.go.kr/kma/archive/pub.jsp?field1=grp&text1=yearbook",
    )
    assert [row["filename"] for row in documents] == [
        "yearbook_2025.pdf",
        "yearbook_2005_1.pdf",
    ]
    assert all(row["download_request"]["method"] == "GET" for row in documents)


def test_kma_parser_rejects_cross_host_download() -> None:
    page = '<a class="down" href="https://evil.example/yearbook_2025.pdf">x</a>'
    with pytest.raises(collector.CollectionError, match="allowlisted"):
        collector.parse_kma_page(page, collector.SOURCE_SPECS[SOURCE_ID].landing_url)


def test_gyeonggi_official_attachment_json_builds_direct_urls() -> None:
    api_url = (
        "https://data.gg.go.kr/portal/data/file/searchFileData.do?"
        "infId=5XP8QWO5939KP255ST221203200&infSeq=1"
    )
    payload = {
        "data": [
            {
                "infId": "5XP8QWO5939KP255ST221203200",
                "infSeq": 1,
                "fileSeq": 2941,
                "viewFileNm": "43. 대형건설공사장-도로(건설안전과)",
                "fileExt": "HWP",
                "fileSize": 197632,
                "viewCnt": 10,
            }
        ]
    }
    documents = collector.parse_gyeonggi_payload(payload, api_url)
    assert len(documents) == 1
    document = documents[0]
    assert document["filename"].endswith(".hwp")
    assert document["declared_size_bytes"] == 197632
    assert "fileSeq=2941" in document["download_request"]["url"]


def test_gyeonggi_attachment_identity_mismatch_is_rejected() -> None:
    payload = {
        "data": [
            {
                "infId": "another-dataset",
                "infSeq": 1,
                "fileSeq": 1,
                "viewFileNm": "wrong",
                "fileExt": "HWP",
            }
        ]
    }
    with pytest.raises(collector.CollectionError, match="identity mismatch"):
        collector.parse_gyeonggi_payload(payload, "https://data.gg.go.kr/api")


def test_agris_json_builds_post_download_contract() -> None:
    payload = {
        "new_ReportList": [
            {
                "reportSeq": 239,
                "reportNm": "2025년 충청남도 당진시 골재자원조사 보고서",
                "eyear": "2025",
                "sdNm": "충남",
                "sggNm": "당진시",
                "pubOrg": "국토교통부",
                "execOrg": "한국지질자원연구원",
                "fileList": [
                    {
                        "atchFileId": "abc123",
                        "fileSn": "1",
                        "fileStreCours": "encoded%3D%3D",
                        "orignlFileNm": "당진시골재보고서.pdf",
                        "fileSize": 200671769,
                        "fileGb": "2",
                    }
                ],
            }
        ]
    }
    documents = collector.parse_agris_payload(payload, "https://www.agris.go.kr/api")
    assert len(documents) == 1
    request = documents[0]["download_request"]
    assert request["method"] == "POST"
    assert request["url"] == "https://www.agris.go.kr/egov/com/nomFileDown.do"
    assert request["form"]["atchFileId"] == "abc123"
    assert documents[0]["declared_size_bytes"] == 200671769


def test_snapshot_is_exact_hashed_and_immutable(tmp_path: Path) -> None:
    payload = collector.HttpPayload(
        body=b'{"license":"KOGL-1"}',
        final_url="https://www.data.go.kr/catalog/1/fileData.json",
        mime="application/json",
        charset="utf-8",
        status=200,
    )
    evidence = collector._snapshot(
        tmp_path,
        source_id=SOURCE_ID,
        kind="license",
        sequence=1,
        payload=payload,
        suffix=".json",
    )
    saved = tmp_path / evidence["snapshot_path"]
    assert saved.read_bytes() == payload.body
    assert evidence["snapshot_sha256"] == hashlib.sha256(payload.body).hexdigest()
    with pytest.raises(collector.CollectionError, match="refusing to replace"):
        collector._snapshot(
            tmp_path,
            source_id=SOURCE_ID,
            kind="license",
            sequence=1,
            payload=payload,
            suffix=".json",
        )


def test_unique_run_directory_never_reuses_existing_path(tmp_path: Path) -> None:
    first = collector.create_unique_run_dir(tmp_path, "fixed-run")
    assert first.is_dir()
    with pytest.raises(collector.CollectionError, match="already exists"):
        collector.create_unique_run_dir(tmp_path, "fixed-run")
    with pytest.raises(collector.CollectionError, match="unsafe"):
        collector.create_unique_run_dir(tmp_path, "../escape")


def test_pdf_download_is_mime_magic_capped_hashed_and_atomic(tmp_path: Path) -> None:
    body = b"%PDF-1.7\nsmall-fixture"
    response = _FakeResponse(body, content_length=len(body))
    client = _FakeClient(response)
    result = collector.download_document(
        client, tmp_path, _pdf_document(), max_bytes=1024
    )
    assert result["status"] == "downloaded"
    assert result["size_bytes"] == len(body)
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert (tmp_path / result["path"]).read_bytes() == body
    assert not list(tmp_path.rglob("*.part"))


def test_html_error_page_is_rejected_even_as_octet_stream(tmp_path: Path) -> None:
    response = _FakeResponse(
        b"<!doctype html><title>Access denied</title>",
        content_type="application/octet-stream",
    )
    with pytest.raises(collector.CollectionError, match="HTML error page"):
        collector.download_document(
            _FakeClient(response), tmp_path, _pdf_document(), max_bytes=1024
        )
    assert not list(tmp_path.rglob("*.pdf"))
    assert not list(tmp_path.rglob("*.part"))


def test_download_size_cap_is_enforced_before_streaming(tmp_path: Path) -> None:
    response = _FakeResponse(
        b"%PDF-1.7\nbody",
        content_length=10_000,
    )
    with pytest.raises(collector.CollectionError, match="exceeds cap"):
        collector.download_document(
            _FakeClient(response), tmp_path, _pdf_document(), max_bytes=100
        )
    assert not list(tmp_path.rglob("*.part"))


def test_discover_mode_and_download_flag_cannot_bypass_license_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({"sources": [_registry_source()]}), encoding="utf-8"
    )
    evidence = {
        "license_code": "KOGL-1",
        "snapshot_sha256": "a" * 64,
        "snapshot_path": "evidence/license.json",
    }

    def fake_license(*_args: object, **_kwargs: object) -> dict:
        return evidence

    def fake_discover(
        *_args: object, **_kwargs: object
    ) -> tuple[list[dict], list, dict]:
        document = _pdf_document()
        collector._bind_evidence(document, evidence)
        return [document], [], {
            "available_source_record_count": 1,
            "available_attachment_count": 1,
            "truncated_by_max_items": False,
        }

    def forbidden_download(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError("license-held document must never be downloaded")

    monkeypatch.setattr(collector, "_license_evidence", fake_license)
    monkeypatch.setitem(collector.DISCOVERERS, SOURCE_ID, fake_discover)
    monkeypatch.setattr(collector, "download_document", forbidden_download)

    for explicit_download in (False, True):
        run_dir, manifest = collector.collect(
            registry_path=registry_path,
            output_root=tmp_path / "runs",
            source_ids=[SOURCE_ID],
            download=explicit_download,
            max_bytes=1024,
            max_items=None,
            timeout=1,
        )
        assert run_dir.is_dir()
        assert manifest["status"] == "license_hold"
        status = manifest["documents"][0]["download_result"]["status"]
        expected = "skipped_license_hold" if explicit_download else "not_requested"
        assert status == expected
        assert manifest["summary"]["documents_on_license_hold"] == 1

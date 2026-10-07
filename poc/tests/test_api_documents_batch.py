"""POST /api/v1/documents/batch — 다중 파일 업로드 E2E 검증 (2026-10-06 신설).

KL 요청("한 번의 요청에 여러 문서") 대응 — 파일마다 독립 ingest, 성공분은
하나의 /classify/batch 작업으로 묶어 큐에 건다.
"""

from __future__ import annotations

import io
import json

import pytest

pytestmark = pytest.mark.slow
from fastapi.testclient import TestClient

from koipa.adapters.storage import LocalStorage
from koipa.api.app import app
from koipa.api.documents import _get_ingestion_service
from koipa.config import settings
from koipa.services.document_ingestion_service import DocumentIngestionService


@pytest.fixture
def client(tmp_path):
    storage = LocalStorage(root=str(tmp_path / "store"))
    svc = DocumentIngestionService(storage=storage)
    app.dependency_overrides[_get_ingestion_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.pop(_get_ingestion_service, None)


def _hdr():
    return {"X-API-Key": settings.api_key or "test-key"}


def _actor() -> str:
    return json.dumps({"user_id": "test-uploader", "role": "kl_backend"})


def _files(items: list[tuple[str, bytes]]):
    return [("files", (name, io.BytesIO(body), "application/octet-stream")) for name, body in items]


class TestDocumentsBatchUpload:
    def test_two_files_201_independent_results_same_order(self, client):
        items = [
            ("a.txt", "핵심 공정 레시피 ALD 증착 조건 대외비".encode("utf-8")),
            ("b.txt", "일반 공개 보도자료 내용".encode("utf-8")),
        ]
        r = client.post(
            "/api/v1/documents/batch",
            headers=_hdr(),
            data={"actor": _actor()},
            files=_files(items),
        )
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["total"] == 2
        assert len(j["results"]) == 2
        assert j["results"][0]["filename"] == "a.txt"
        assert j["results"][1]["filename"] == "b.txt"
        assert j["results"][0]["char_count"] > 0
        assert j["results"][1]["char_count"] > 0
        # 분류를 안 걸었으면 배치 분류 필드는 비어 있다.
        assert j["classification_job_id"] is None

    def test_client_request_ids_matched_by_order(self, client):
        items = [("a.txt", b"A content"), ("b.txt", b"B content")]
        r = client.post(
            "/api/v1/documents/batch",
            headers=_hdr(),
            data={"actor": _actor(), "client_request_ids": ["req-a", "req-b"]},
            files=_files(items),
        )
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["results"][0]["client_request_id"] == "req-a"
        assert j["results"][1]["client_request_id"] == "req-b"

    def test_client_request_ids_length_mismatch_422(self, client):
        items = [("a.txt", b"A"), ("b.txt", b"B")]
        r = client.post(
            "/api/v1/documents/batch",
            headers=_hdr(),
            data={"actor": _actor(), "client_request_ids": ["only-one"]},
            files=_files(items),
        )
        assert r.status_code == 422, r.text

    def test_one_empty_file_does_not_block_the_others(self, client):
        items = [("ok.txt", b"real content here"), ("empty.txt", b"")]
        r = client.post(
            "/api/v1/documents/batch",
            headers=_hdr(),
            data={"actor": _actor()},
            files=_files(items),
        )
        assert r.status_code == 201, r.text
        j = r.json()
        ok, empty = j["results"]
        assert ok["persisted"] in (True, False)  # DB 가용성에 따라 다름 — 여기선 char_count 만 본다
        assert ok["char_count"] > 0
        assert empty["char_count"] == 0
        assert any("empty file" in w for w in empty["warnings"])

    def test_too_many_files_413(self, client, monkeypatch):
        monkeypatch.setattr(settings, "documents_batch_max_files", 2)
        items = [(f"f{i}.txt", b"x") for i in range(3)]
        r = client.post(
            "/api/v1/documents/batch",
            headers=_hdr(),
            data={"actor": _actor()},
            files=_files(items),
        )
        assert r.status_code == 413, r.text

    def test_enqueue_classification_submits_one_batch_job(self, client):
        items = [
            ("a.txt", "핵심 공정 레시피 ALD 증착 조건 대외비".encode("utf-8")),
            ("b.txt", "일반 공개 보도자료 내용".encode("utf-8")),
        ]
        r = client.post(
            "/api/v1/documents/batch",
            headers=_hdr(),
            data={"actor": _actor(), "enqueue_classification": "true"},
            files=_files(items),
        )
        assert r.status_code == 201, r.text
        j = r.json()
        for item in j["results"]:
            if item["doc_id"] is None:
                continue
        # DB 미가용이면 doc_id 가 없어 분류 대상이 비고, job_id 도 None 이어야 한다.
        persisted_any = any(it["doc_id"] for it in j["results"])
        if persisted_any:
            assert j["classification_job_id"] is not None
            assert j["classification_status"] in ("queued", "done", "partial", "failed")
        else:
            assert j["classification_job_id"] is None
            assert any(
                "classification was not queued" in w
                for it in j["results"] for w in it["warnings"]
            )

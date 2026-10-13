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
from pydantic import ValidationError

from koipa.adapters.storage import LocalStorage
from koipa.api.app import app
from koipa.api.documents import _get_ingestion_service
from koipa.config import Settings, settings
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
    def test_total_file_bytes_limit_must_be_positive(self):
        assert Settings(_env_file=None, documents_batch_max_total_mb=3).documents_batch_max_total_mb == 3
        with pytest.raises(ValidationError, match="documents_batch_max_total_mb"):
            Settings(_env_file=None, documents_batch_max_total_mb=0)

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

    def test_per_file_limit_is_reported_per_item_without_rejecting_batch(self, client, monkeypatch):
        monkeypatch.setattr(settings, "max_upload_mb", 1)
        items = [
            ("too-large.txt", b"x" * (1024 * 1024 + 1)),
            ("small.txt", b"small document"),
        ]
        r = client.post(
            "/api/v1/documents/batch",
            headers=_hdr(),
            data={"actor": _actor()},
            files=_files(items),
        )
        # 개별 파일 초과는 기존 계약대로 201의 항목별 warning이다.
        assert r.status_code == 201, r.text
        first, second = r.json()["results"]
        assert first["filename"] == "too-large.txt"
        assert first["persisted"] is False
        assert any("file too large" in warning for warning in first["warnings"])
        assert second["filename"] == "small.txt"

    def test_total_file_bytes_limit_rejects_entire_batch_with_exact_413(self, client, monkeypatch):
        monkeypatch.setattr(settings, "documents_batch_max_total_mb", 1)
        items = [
            ("first.txt", b"a" * (700 * 1024)),
            ("second.txt", b"b" * (400 * 1024)),
        ]
        r = client.post(
            "/api/v1/documents/batch",
            headers=_hdr(),
            data={"actor": _actor()},
            files=_files(items),
        )
        assert r.status_code == 413, r.text
        detail = r.json()["detail"]
        assert detail == {
            "code": "KOIPA_DOCUMENT_BATCH_TOTAL_TOO_LARGE",
            "message": "total uploaded file bytes exceed documents_batch_max_total_mb",
            "total_file_bytes": 1100 * 1024,
            "max_total_file_bytes": 1024 * 1024,
        }

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

    def test_enqueue_classification_batch_job_results_carry_client_request_id(self, client):
        """[2026-10-13 결함 수정, KL 리포트] 배치 업로드 응답에는 파일별 client_request_id 가
        정상 반영됐지만, 분류 작업 결과 조회(GET /classify/jobs/{job_id})에서는 null 로
        돌아왔다 — 업로드 응답이 아니라 분류 작업 결과 쪽을 확인한다(업로드 응답은
        test_client_request_ids_matched_by_order 가 이미 커버).

        TESTING 환경은 브로커가 없어 in-process 로 즉시 처리되므로(submit_batch), 업로드
        응답이 돌아온 시점에 이미 작업이 done 상태다 — 폴링 불필요.
        """
        items = [
            ("a.txt", "핵심 공정 레시피 ALD 증착 조건 대외비".encode("utf-8")),
            ("b.txt", "일반 공개 보도자료 내용".encode("utf-8")),
        ]
        r = client.post(
            "/api/v1/documents/batch",
            headers=_hdr(),
            data={
                "actor": _actor(),
                "enqueue_classification": "true",
                "client_request_ids": ["req-a", "req-b"],
            },
            files=_files(items),
        )
        assert r.status_code == 201, r.text
        j = r.json()
        # 업로드 응답 레벨은 기존에도 정상이었다 — 여기서도 재확인.
        assert j["results"][0]["client_request_id"] == "req-a"
        assert j["results"][1]["client_request_id"] == "req-b"

        if j["classification_job_id"] is None:
            pytest.skip("DB 미가용 — 이 환경에서는 분류 작업이 아예 안 걸린다")

        job = client.get(
            f"/api/v1/classify/jobs/{j['classification_job_id']}", headers=_hdr(),
        )
        assert job.status_code == 200, job.text
        jr = job.json()
        assert jr["results"] is not None and len(jr["results"]) == 2
        by_doc_id = {
            it["doc_id"]: it["client_request_id"]
            for it, up in zip(jr["results"], j["results"])
            if up["doc_id"] is not None
        }
        # 업로드 응답의 client_request_id 와 1:1로 맞아야 한다(doc_id 로 교차 확인).
        upload_crid_by_doc_id = {
            up["doc_id"]: up["client_request_id"] for up in j["results"] if up["doc_id"] is not None
        }
        assert by_doc_id == upload_crid_by_doc_id
        assert set(upload_crid_by_doc_id.values()) == {"req-a", "req-b"}

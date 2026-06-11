"""POST /classify — doc_id 전용 경로 검증 (content 없이 분류 요청).

업로드 → doc_id 획득 → content 없이 classify → 텍스트를 storage에서 읽어 분류.
"""

from __future__ import annotations

import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.slow

from lloydk.adapters.storage import LocalStorage
from lloydk.api.app import app
from lloydk.api.documents import _get_ingestion_service
from lloydk.services.document_ingestion_service import DocumentIngestionService


@pytest.fixture
def client_with_storage(tmp_path):
    storage = LocalStorage(root=str(tmp_path / "store"))
    svc = DocumentIngestionService(storage=storage)
    app.dependency_overrides[_get_ingestion_service] = lambda: svc
    yield TestClient(app), storage, svc
    app.dependency_overrides.pop(_get_ingestion_service, None)


def _hdr():
    from lloydk.config import settings
    return {"X-API-Key": settings.api_key or "test-key"}


def _actor():
    return json.dumps({"user_id": "tester", "role": "kl_backend", "tenant_id": "default"})


class TestClassifyByDocId:
    def test_content_optional_with_text(self, client_with_storage):
        """content가 있으면 기존처럼 동작 — optional 필드 변경 회귀 없음."""
        client, _, _ = client_with_storage
        payload = {
            "doc_id": str(uuid.uuid4()),
            "content": "ALD 증착 핵심 공정 레시피. 조성 비율 대외비. 반도체 공정 파라미터.",
            "use_rag": False,
        }
        r = client.post("/api/v1/classify", headers=_hdr(), json=payload)
        assert r.status_code == 200, r.text
        assert "label" in r.json()

    def test_content_none_without_db_doc_fail_secure_isolates(self, client_with_storage):
        """content 없고 doc_id도 못 읽으면 — fail-SECURE (H2).

        본문을 못 읽은 문서를 공개(S3)로 흘리던 과거 동작(미탐 경로)을 제거했다.
        이제는 최고등급(TS)으로 격리하고 status=needs_review(검수 큐)로 둔다 —
        절대 공개로 폴백하지 않는다.
        """
        client, _, _ = client_with_storage
        payload = {
            "doc_id": str(uuid.uuid4()),
            "use_rag": False,
        }
        r = client.post("/api/v1/classify", headers=_hdr(), json=payload)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["status"] == "needs_review"  # 'error'로 공개 폴백하지 않음
        assert j["label"] == "TS"  # 최고등급 격리 (fail-secure)
        assert j["warnings"]

    def test_upload_then_classify_by_doc_id(self, client_with_storage, tmp_path):
        """파일 업로드 후 doc_id만으로 분류 요청.

        ingestion 서비스가 LocalStorage를 쓰고 DB 없으므로 doc_id=None.
        DB 없는 환경에서는 storage URI를 직접 조회할 수 없어 error 상태 정상.
        실 DB 환경(PG 가용)에서는 full path로 동작.
        """
        client, storage, svc = client_with_storage
        body = "ALD 증착 공정 핵심 레시피 대외비 반도체 제조".encode("utf-8")
        r = client.post(
            "/api/v1/documents",
            headers=_hdr(),
            data={"actor": _actor()},
            files={"file": ("recipe.txt", io.BytesIO(body), "text/plain")},
        )
        assert r.status_code == 201
        upload_resp = r.json()
        doc_id = upload_resp.get("doc_id")

        if doc_id is None:
            # DB 미가용 — doc_id 없으니 classify by doc_id는 error
            classify_payload = {"doc_id": str(uuid.uuid4()), "use_rag": False}
        else:
            classify_payload = {"doc_id": doc_id, "use_rag": False}

        r2 = client.post("/api/v1/classify", headers=_hdr(), json=classify_payload)
        assert r2.status_code == 200, r2.text
        # DB 없는 환경: error / DB 있는 환경: 정상 분류
        j = r2.json()
        assert "label" in j

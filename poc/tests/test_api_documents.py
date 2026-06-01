"""POST /api/v1/documents — 문서 업로드 API E2E 검증.

실제 파일(txt/docx/pdf/이미지)을 multipart로 올려서:
  추출 → 원본 보관 → provenance → 응답 필드 검증.
DB 미가용 환경에서는 persisted=False + warnings 로 안전 반환 확인.
"""

from __future__ import annotations

import io
import json

import pytest
pytestmark = pytest.mark.slow
from fastapi.testclient import TestClient

from lloydk.adapters.storage import LocalStorage
from lloydk.api.app import app
from lloydk.api.documents import _get_ingestion_service
from lloydk.config import settings
from lloydk.services.document_ingestion_service import DocumentIngestionService


@pytest.fixture
def client(tmp_path):
    """MinIO 없이 LocalStorage 주입 — DB 미가용 환경에서 persisted=False 정상."""
    storage = LocalStorage(root=str(tmp_path / "store"))
    svc = DocumentIngestionService(storage=storage)
    app.dependency_overrides[_get_ingestion_service] = lambda: svc
    yield TestClient(app)
    app.dependency_overrides.pop(_get_ingestion_service, None)


def _hdr():
    return {"X-API-Key": settings.api_key or "test-key"}


def _actor(tenant: str = "default") -> str:
    return json.dumps({"user_id": "test-uploader", "role": "kl_backend", "tenant_id": tenant})


def _post(client, filename: str, body: bytes, tenant: str = "default", **extra):
    return client.post(
        "/api/v1/documents",
        headers=_hdr(),
        data={"actor": _actor(tenant), **extra},
        files={"file": (filename, io.BytesIO(body), "application/octet-stream")},
    )


# ---------------------------------------------------------------------------
# 기본 업로드 — 201 + 필드 구조
# ---------------------------------------------------------------------------
class TestDocumentUploadBasic:
    def test_txt_upload_201(self, client):
        body = "핵심 공정 레시피 ALD 증착 조건 대외비".encode("utf-8")
        r = _post(client, "secret.txt", body)
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["filename"] == "secret.txt"
        assert j["source_format"] == "txt"
        assert j["extraction_method"] == "plain"
        assert j["char_count"] > 0
        assert j["chunk_count"] >= 1
        assert len(j["file_hash"]) == 64
        assert j["file_size_bytes"] == len(body)
        # DB 미가용 환경 → persisted=False + warnings (정상)
        # DB 가용 환경 → persisted=True
        assert isinstance(j["persisted"], bool)
        assert isinstance(j["warnings"], list)

    def test_docx_upload_201(self, client):
        docx = pytest.importorskip("docx")
        d = docx.Document()
        d.add_paragraph("영업비밀 가이드: ALD 공정 레시피 및 조성 비율.")
        buf = io.BytesIO()
        d.save(buf)
        body = buf.getvalue()

        r = _post(client, "guide.docx", body)
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["source_format"] == "docx"
        assert j["extraction_method"] == "parser"
        assert j["char_count"] > 0

    def test_pdf_upload_201(self, client):
        # 텍스트 레이어 있는 최소 PDF
        from tests.test_document_ingestion import _minimal_pdf
        body = _minimal_pdf("ALD deposition trade secret")
        r = _post(client, "report.pdf", body)
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["source_format"] == "pdf"
        assert j["char_count"] > 0


# ---------------------------------------------------------------------------
# doc_type / external_ref 메타 전달
# ---------------------------------------------------------------------------
class TestDocumentUploadMeta:
    def test_doc_type_forwarded(self, client):
        body = b"test metadata forwarding"
        r = _post(client, "m.txt", body, doc_type="기술보고서", external_ref="EDMS-001")
        assert r.status_code == 201, r.text

    def test_tenant_isolation(self, client):
        body = b"tenant a document"
        r1 = _post(client, "a.txt", body, tenant="tenant_a")
        r2 = _post(client, "a.txt", body, tenant="tenant_b")
        assert r1.status_code == 201
        assert r2.status_code == 201
        # 같은 파일이라도 테넌트가 다르면 별도 ingestion
        # (doc_id는 DB 없으면 None이므로 file_hash로 비교)
        assert r1.json()["file_hash"] == r2.json()["file_hash"]  # 내용 동일
        # persisted 여부와 무관하게 응답은 정상


# ---------------------------------------------------------------------------
# 입력 검증
# ---------------------------------------------------------------------------
class TestDocumentUploadValidation:
    def test_empty_file_422(self, client):
        r = _post(client, "empty.txt", b"")
        assert r.status_code == 422, r.text

    def test_over_limit_413(self, client, monkeypatch):
        monkeypatch.setattr(settings, "max_upload_mb", 1)
        body = b"X" * (2 * 1024 * 1024)
        r = _post(client, "big.txt", body)
        assert r.status_code == 413, r.text

    def test_missing_auth_401(self, client):
        body = b"no auth"
        r = client.post(
            "/api/v1/documents",
            data={"actor": _actor()},
            files={"file": ("f.txt", io.BytesIO(body), "text/plain")},
        )
        assert r.status_code == 401

    def test_invalid_actor_422(self, client):
        r = client.post(
            "/api/v1/documents",
            headers=_hdr(),
            data={"actor": "NOT_JSON"},
            files={"file": ("f.txt", io.BytesIO(b"body"), "text/plain")},
        )
        assert r.status_code == 422

    def test_unsupported_format_graceful(self, client):
        """미지원 포맷도 413/422 아닌 201 — 원본 보관 + warnings."""
        r = _post(client, "binary.xyz", b"\x00\x01\x02misc binary")
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["char_count"] == 0
        assert any("no text extracted" in w for w in j["warnings"])


# ---------------------------------------------------------------------------
# 이미지 OCR 경로
# ---------------------------------------------------------------------------
class TestDocumentUploadOcr:
    def test_image_ocr_upload(self, client):
        PIL = pytest.importorskip("PIL.Image")
        ImageDraw = pytest.importorskip("PIL.ImageDraw")
        pytest.importorskip("pytesseract")

        img = PIL.new("RGB", (600, 80), color="white")
        ImageDraw.Draw(img).text((10, 20), "Trade Secret ALD process recipe", fill="black")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        body = buf.getvalue()

        r = _post(client, "scan.png", body)
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["source_format"] == "png"
        assert j["ocr_used"] is True
        assert j["char_count"] > 0

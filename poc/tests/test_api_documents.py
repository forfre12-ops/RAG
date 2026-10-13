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

from koipa.adapters.storage import LocalStorage
from koipa.api.app import app
from koipa.api.documents import _get_ingestion_service
from koipa.config import settings
from koipa.services.document_ingestion_service import DocumentIngestionService


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


def _actor() -> str:
    return json.dumps({"user_id": "test-uploader", "role": "kl_backend"})


def _post(client, filename: str, body: bytes, **extra):
    return client.post(
        "/api/v1/documents",
        headers=_hdr(),
        data={"actor": _actor(), **extra},
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
        # 2026-09-26 에 뺀 항목 — 추출이 잘렸는지(extraction_complete)·처리 쪽수(pages_processed)는 응답에 없다
        assert "extraction_complete" not in j and "pages_processed" not in j

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
# 등록 때 받는 메타(ICD §3.1~3.3)
# ---------------------------------------------------------------------------
class TestDocumentUploadMeta:
    def test_icd_metadata_forwarded(self, client):
        body = b"test metadata forwarding"
        r = _post(client, "m.txt", body, source_type="internal", security_marking="confidential", access_scope="department")
        assert r.status_code == 201, r.text

    def test_removed_form_fields_are_ignored(self, client):
        """doc_type·external_ref 는 2026-09-26 에 없앴다(저장만 하고 아무도 안 읽었다). 옛 안내서대로 보내도 무시되고 201 이다."""
        r = _post(client, "m.txt", b"legacy form fields", doc_type="기술보고서", external_ref="EDMS-001")
        assert r.status_code == 201, r.text
        assert "doc_type" not in r.json() and "external_ref" not in r.json()

    def test_same_content_same_hash(self, client):
        body = b"same content document"
        r1 = _post(client, "a.txt", body)
        r2 = _post(client, "a.txt", body)
        assert r1.status_code == 201
        assert r2.status_code == 201
        # 같은 내용 → 같은 file_hash (content-addressed)
        assert r1.json()["file_hash"] == r2.json()["file_hash"]


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
# 이미지 — OCR 을 하지 않으므로 본문 없이 등록된다
# ---------------------------------------------------------------------------
def _tiny_png() -> bytes:
    """1x1 흰색 PNG. 이미지 내용은 읽지 않으므로(OCR 안 함) Pillow 없이 만든다."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


class TestDocumentUploadImage:
    def test_image_upload_is_registered_without_text(self, client):
        r = _post(client, "scan.png", _tiny_png())
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["source_format"] == "png"
        assert j["char_count"] == 0
        assert "ocr_used" not in j
        assert any("unsupported" in w for w in j["warnings"])

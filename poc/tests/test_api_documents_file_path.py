"""POST /api/v1/documents — file_path 옵션 (2026-10-06 신설).

KL 요청 — 파일을 업로드하지 않고 같은 VM에 이미 있는 파일을 경로로 등록.
documents_shared_mount_dir 로 지정한 디렉터리 밖을 가리키면 거절(경로조작 방어).
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


class TestDocumentsFilePath:
    def test_disabled_by_default_422(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "documents_shared_mount_dir", "")
        shared = tmp_path / "shared"
        shared.mkdir()
        f = shared / "doc.txt"
        f.write_text("핵심 공정 레시피", encoding="utf-8")

        r = client.post(
            "/api/v1/documents",
            headers=_hdr(),
            data={"actor": _actor(), "file_path": str(f)},
        )
        assert r.status_code == 422, r.text

    def test_reads_file_inside_configured_mount(self, client, monkeypatch, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        f = shared / "doc.txt"
        f.write_text("핵심 공정 레시피 ALD 증착 조건", encoding="utf-8")
        monkeypatch.setattr(settings, "documents_shared_mount_dir", str(shared))

        r = client.post(
            "/api/v1/documents",
            headers=_hdr(),
            data={"actor": _actor(), "file_path": str(f)},
        )
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["filename"] == "doc.txt"
        assert j["char_count"] > 0

    def test_rejects_path_outside_configured_mount(self, client, monkeypatch, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("바깥 파일", encoding="utf-8")
        monkeypatch.setattr(settings, "documents_shared_mount_dir", str(shared))

        r = client.post(
            "/api/v1/documents",
            headers=_hdr(),
            data={"actor": _actor(), "file_path": str(outside)},
        )
        assert r.status_code == 422, r.text
        assert "shared directory" in r.text

    def test_rejects_traversal_out_of_mount(self, client, monkeypatch, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        secret = tmp_path / "secret.env"
        secret.write_text("API_KEY=sensitive", encoding="utf-8")
        monkeypatch.setattr(settings, "documents_shared_mount_dir", str(shared))

        traversal_path = str(shared / ".." / "secret.env")
        r = client.post(
            "/api/v1/documents",
            headers=_hdr(),
            data={"actor": _actor(), "file_path": traversal_path},
        )
        assert r.status_code == 422, r.text

    def test_nonexistent_path_422(self, client, monkeypatch, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        monkeypatch.setattr(settings, "documents_shared_mount_dir", str(shared))

        r = client.post(
            "/api/v1/documents",
            headers=_hdr(),
            data={"actor": _actor(), "file_path": str(shared / "nope.txt")},
        )
        assert r.status_code == 422, r.text

    def test_both_file_and_file_path_422(self, client, monkeypatch, tmp_path):
        shared = tmp_path / "shared"
        shared.mkdir()
        f = shared / "doc.txt"
        f.write_text("content", encoding="utf-8")
        monkeypatch.setattr(settings, "documents_shared_mount_dir", str(shared))

        r = client.post(
            "/api/v1/documents",
            headers=_hdr(),
            data={"actor": _actor(), "file_path": str(f)},
            files={"file": ("other.txt", io.BytesIO(b"x"), "text/plain")},
        )
        assert r.status_code == 422, r.text

    def test_neither_file_nor_file_path_422(self, client):
        r = client.post(
            "/api/v1/documents",
            headers=_hdr(),
            data={"actor": _actor()},
        )
        assert r.status_code == 422, r.text

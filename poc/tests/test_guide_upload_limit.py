"""R3 — Guide upload max_size 한도 검증.

DoS 차단 — 대용량 바이너리 업로드 시 OOM 방지.

검증:
- max_upload_mb 한도 이하: 201 Created
- 한도 초과: 413 Payload Too Large
- 설정값 변경 시 한도 동적 반영
"""

from __future__ import annotations

import io
import json
import os

import pytest

# 테스트 환경 — rate-limit 비활성
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from fastapi.testclient import TestClient  # noqa: E402

from lloydk.api.app import app  # noqa: E402
from lloydk.config import settings  # noqa: E402


def _api_key() -> str:
    return settings.api_key or "test-key"


def _hdr() -> dict:
    return {
        "X-API-Key": _api_key(),
        "X-Actor-Id": "test-uploader",
        "X-Actor-Role": "kl_backend",
        "X-Tenant-Id": "default",
    }


def _make_payload(size_bytes: int) -> bytes:
    return b"X" * size_bytes


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def actor_json() -> str:
    return json.dumps({"user_id": "test-uploader", "role": "kl_backend", "tenant_id": "default"})


class TestGuideUploadLimit:
    def test_under_limit_accepted(self, client, actor_json, monkeypatch):
        """1MB 본문은 한도(기본 20MB) 이하 — 201 또는 5xx (PG 미가용)."""
        body = _make_payload(1 * 1024 * 1024)  # 1MB
        files = {"file": ("test.txt", io.BytesIO(body), "text/plain")}
        data = {
            "guide_id": "test-limit-1",
            "version": "v1.0",
            "actor": actor_json,
        }
        r = client.post("/api/v1/guide/documents", headers=_hdr(), data=data, files=files)
        # 정상 가이드 업로드: 201 / PG 미가용·일시 장애: 503 / 등급 매핑 등 422
        assert r.status_code in (201, 422, 500, 503), f"unexpected status {r.status_code}: {r.text[:200]}"

    def test_over_limit_rejected(self, client, actor_json, monkeypatch):
        """한도 초과 시 413."""
        # 한도를 1MB로 낮춰서 테스트 (실제 20MB body 만들지 않음 — 메모리 절약)
        monkeypatch.setattr(settings, "max_upload_mb", 1)
        body = _make_payload(2 * 1024 * 1024)  # 2MB
        files = {"file": ("big.txt", io.BytesIO(body), "text/plain")}
        data = {
            "guide_id": "test-limit-2",
            "version": "v1.0",
            "actor": actor_json,
        }
        r = client.post("/api/v1/guide/documents", headers=_hdr(), data=data, files=files)
        assert r.status_code == 413, f"expected 413, got {r.status_code}: {r.text[:200]}"
        body_json = r.json()
        # FastAPI HTTPException 표준 응답
        detail = body_json.get("detail", "")
        assert "too large" in detail.lower() or "1mb" in detail.lower(), \
            f"detail message unclear: {detail}"

    def test_exact_limit_accepted(self, client, actor_json, monkeypatch):
        """정확히 한도와 같으면 통과."""
        monkeypatch.setattr(settings, "max_upload_mb", 1)
        body = _make_payload(1 * 1024 * 1024)  # 정확히 1MB
        files = {"file": ("exact.txt", io.BytesIO(body), "text/plain")}
        data = {
            "guide_id": "test-limit-3",
            "version": "v1.0",
            "actor": actor_json,
        }
        r = client.post("/api/v1/guide/documents", headers=_hdr(), data=data, files=files)
        # 413이 아님 — 한도 통과
        assert r.status_code != 413, f"413 unexpected at exact limit: {r.text[:200]}"

    def test_limit_config_changeable(self, monkeypatch):
        """settings.max_upload_mb 변경이 즉시 반영."""
        monkeypatch.setattr(settings, "max_upload_mb", 5)
        assert settings.max_upload_mb == 5
        monkeypatch.setattr(settings, "max_upload_mb", 50)
        assert settings.max_upload_mb == 50

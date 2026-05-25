"""FastAPI API — 인증/healthz/classify."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lloydk.api.app import app
from lloydk.config import settings


def test_healthz_ok():
    with TestClient(app) as cli:
        r = cli.get("/api/v1/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "uptime_sec" in body


def test_classify_requires_api_key():
    """X-API-Key 헤더가 없으면 401."""
    with TestClient(app) as cli:
        r = cli.post(
            "/api/v1/classify",
            json={"doc_id": "x", "content": "특급기밀 내용"},
        )
        # 헤더 누락은 422(Header(...)) 또는 401
        assert r.status_code in (401, 422)


def test_classify_rejects_invalid_api_key():
    with TestClient(app) as cli:
        r = cli.post(
            "/api/v1/classify",
            headers={"X-API-Key": "wrong-key"},
            json={"doc_id": "x", "content": "내용"},
        )
        assert r.status_code == 401


def test_classify_full_response_shape():
    with TestClient(app) as cli:
        r = cli.post(
            "/api/v1/classify",
            headers={"X-API-Key": settings.api_key},
            json={
                "doc_id": "smoke-ts",
                "tenant_id": "test",
                "content": "특급기밀 차세대 제품 설계도 핵심 원천기술 M&A 계획",
                "use_rag": False,
                "return_evidence": True,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # OpenAPI 스키마 핵심 필드 모두 존재
        for key in ("inference_id", "doc_id", "label", "confidence", "scores",
                    "model_version", "elapsed_ms", "status"):
            assert key in data, f"missing {key}"
        assert data["label"] == "TS"
        assert data["scores"]["TS"] >= max(
            data["scores"]["S1"], data["scores"]["S2"], data["scores"]["S3"]
        )


def test_request_id_header_returned():
    with TestClient(app) as cli:
        r = cli.get("/api/v1/healthz")
        assert "x-request-id" in {k.lower() for k in r.headers}

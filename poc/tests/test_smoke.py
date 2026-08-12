import pytest
pytestmark = pytest.mark.slow
from fastapi.testclient import TestClient
from koipa.api.app import app
from koipa.config import settings


def test_healthz():
    with TestClient(app) as cli:
        r = cli.get("/api/v1/healthz")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["operational_config"]["rag"]["embedding_model"] == "nlpai-lab/KURE-v1"
        assert data["operational_config"]["rag"]["search_mode"] == "hybrid"
        assert data["operational_config"]["rag"]["chunk_size"] == 1200
        assert "readiness" in data
        assert data["readiness"]["status"] in {"ok", "missing", "error"}


def test_classify_rule_fallback():
    """모델 가중치 없을 때 M3 규칙 폴백으로 응답하는지."""
    payload = {
        "doc_id": "t-1",
        "content": "본 문서는 미공개 사업계획이며, 핵심기술의 전략과 매출 전망을 포함한다. "
                   "유출 시 손해배상 가능성이 있다.",
    }
    with TestClient(app) as cli:
        r = cli.post(
            "/api/v1/classify",
            headers={"X-API-Key": settings.api_key},
            json=payload,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["label"] in {"TS", "S1", "S2", "S3"}
        assert "model_version" in data

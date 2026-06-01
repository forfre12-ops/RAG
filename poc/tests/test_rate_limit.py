"""Rate-limit 동작 검증 (J4).

- 한도 내 호출은 모두 200/202 통과
- 한도 초과 시 HTTP 429 + Retry-After 헤더
- RATE_LIMIT_DISABLED=1일 때는 한도 무시 (대량 호출도 통과)
"""

from __future__ import annotations

import os

import pytest
pytestmark = pytest.mark.slow
from fastapi.testclient import TestClient

from lloydk.api.app import app
from lloydk.api.rate_limit import limiter
from lloydk.config import settings


HDR = {"X-API-Key": settings.api_key, "X-Tenant-Id": "rate-test-tenant"}


@pytest.fixture
def enable_limiter():
    """rate-limit를 임시 활성화. 카운터 초기화 + 종료 시 비활성 복원."""
    prev_enabled = limiter.enabled
    prev_env = os.environ.get("RATE_LIMIT_DISABLED")
    limiter.enabled = True
    limiter.reset()
    os.environ["RATE_LIMIT_DISABLED"] = "0"
    try:
        yield limiter
    finally:
        limiter.enabled = prev_enabled
        limiter.reset()
        if prev_env is None:
            os.environ.pop("RATE_LIMIT_DISABLED", None)
        else:
            os.environ["RATE_LIMIT_DISABLED"] = prev_env


def _classify_payload(doc_id: str = "rl-test") -> dict:
    return {
        "doc_id": doc_id,
        "tenant_id": "rate-test-tenant",
        "content": "특급기밀 차세대 제품 설계도",
        "use_rag": False,
    }


def test_rate_limit_under_threshold_passes(enable_limiter):
    """/classify 60/min 한도 내(예: 5건)는 전부 200."""
    with TestClient(app) as cli:
        for i in range(5):
            r = cli.post("/api/v1/classify", headers=HDR, json=_classify_payload(f"rl-{i}"))
            assert r.status_code == 200, f"iter {i}: {r.status_code} {r.text}"


def test_rate_limit_exceeds_returns_429(enable_limiter):
    """/synth/generate 10/min 한도 초과 → 429 + Retry-After."""
    payload = {
        "target_grade": "S2",
        "count": 1,
        "llm_provider": "noop",
        "actor": {"user_id": "rl-tester", "role": "admin"},
    }
    with TestClient(app) as cli:
        # 한도(10건)까지 다 보낸 뒤 1건 추가 → 11번째에서 429
        last_status = None
        for _ in range(11):
            r = cli.post("/api/v1/synth/generate", headers=HDR, json=payload)
            last_status = r.status_code
            if last_status == 429:
                break
        assert last_status == 429, f"expected 429 within 11 calls, last={last_status}"
        assert "retry-after" in {k.lower() for k in r.headers}
        body = r.json()
        assert body["code"] == "LLOYDK_RATE_LIMIT"
        assert body["retry_after_sec"] >= 0


def test_rate_limit_disabled_env_bypasses(monkeypatch):
    """RATE_LIMIT_DISABLED=1이면 한도 초과 호출도 모두 통과."""
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    prev_enabled = limiter.enabled
    limiter.enabled = False
    limiter.reset()
    try:
        payload = {
            "target_grade": "S2",
            "count": 1,
            "llm_provider": "noop",
            "actor": {"user_id": "rl-bypass", "role": "admin"},
        }
        with TestClient(app) as cli:
            # 한도(10)보다 많은 12건 호출 — 비활성이므로 모두 202
            statuses = []
            for _ in range(12):
                r = cli.post("/api/v1/synth/generate", headers=HDR, json=payload)
                statuses.append(r.status_code)
            assert all(s == 202 for s in statuses), f"unexpected statuses: {statuses}"
    finally:
        limiter.enabled = prev_enabled
        limiter.reset()


def test_rate_limit_tenant_isolation(enable_limiter):
    """서로 다른 X-Tenant-Id는 독립 카운터 — 한 테넌트가 한도 채워도 다른 테넌트는 통과."""
    payload = {
        "target_grade": "S2",
        "count": 1,
        "llm_provider": "noop",
        "actor": {"user_id": "rl-isolated", "role": "admin"},
    }
    hdr_a = {"X-API-Key": settings.api_key, "X-Tenant-Id": "tenant-a"}
    hdr_b = {"X-API-Key": settings.api_key, "X-Tenant-Id": "tenant-b"}
    with TestClient(app) as cli:
        # tenant-a 한도까지 채움
        for _ in range(10):
            cli.post("/api/v1/synth/generate", headers=hdr_a, json=payload)
        # tenant-a 11번째 호출 → 429
        r_a = cli.post("/api/v1/synth/generate", headers=hdr_a, json=payload)
        # tenant-b 1번째 호출 → 202 (독립 카운터)
        r_b = cli.post("/api/v1/synth/generate", headers=hdr_b, json=payload)
        assert r_a.status_code == 429
        assert r_b.status_code == 202

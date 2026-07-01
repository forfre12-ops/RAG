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


HDR = {"X-API-Key": settings.api_key}


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


def test_rate_limit_shares_ip_bucket_for_same_client(enable_limiter):
    """[M-ratelimit-key] rate-limit 버킷 키는 KL cred(인증된 actor) 또는 IP 폴백.

    tenant 제거: 격리는 KL 포털 전담. 같은 클라이언트(같은 cred/IP)는 같은 버킷을
    공유하므로, 한도를 채우면 후속 요청은 막혀야 한다.
    """
    payload = {
        "target_grade": "S2",
        "count": 1,
        "llm_provider": "noop",
        "actor": {"user_id": "rl-isolated", "role": "admin"},
    }
    with TestClient(app) as cli:
        # 한도까지 채움
        for _ in range(10):
            cli.post("/api/v1/synth/generate", headers=HDR, json=payload)
        # 11번째 → 429 (버킷 고갈)
        r_1 = cli.post("/api/v1/synth/generate", headers=HDR, json=payload)
        # 같은 cred/IP → 같은 버킷 공유 → 역시 429
        r_2 = cli.post("/api/v1/synth/generate", headers=HDR, json=payload)
        assert r_1.status_code == 429
        assert r_2.status_code == 429

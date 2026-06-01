"""M1 — /classify/batch 경계값 가드.

검증 대상: lloydk/api/async_classify.py classify_batch
- 빈 배열   → 400 (의미 없는 요청 가드)
- 999건    → 202 (한도 이하)
- 1000건   → 202 (한도 경계 — 정확히 1000은 허용)
- 1001건   → 거부 (코드는 413 payload too large, 의도는 >1000 거부)
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lloydk.api.app import app
from lloydk.config import settings

import pytest

pytestmark = pytest.mark.slow


HDR = {"X-API-Key": settings.api_key}


def _docs(n: int) -> dict:
    """크기 n의 batch payload 생성. 내용은 짧게 — 메모리·시간 절약."""
    return {"documents": [{"doc_id": f"b{i}", "content": "x"} for i in range(n)]}


def test_batch_empty_rejected_400():
    """빈 documents 배열은 400으로 거부."""
    with TestClient(app) as cli:
        r = cli.post("/api/v1/classify/batch", headers=HDR, json={"documents": []})
        assert r.status_code == 400, r.text
        assert "documents" in r.json().get("detail", "").lower()


def test_batch_999_accepted_202():
    """한도 이하(999) → 202 Accepted + total=999."""
    with TestClient(app) as cli:
        r = cli.post("/api/v1/classify/batch", headers=HDR, json=_docs(999))
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["total"] == 999


def test_batch_1000_boundary_accepted_202():
    """경계값 정확히 1000 → 허용 (>1000만 거부이므로 1000은 통과)."""
    with TestClient(app) as cli:
        r = cli.post("/api/v1/classify/batch", headers=HDR, json=_docs(1000))
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["total"] == 1000


def test_batch_1001_rejected():
    """1001건은 거부. 코드는 413(payload too large)으로 응답하며,
    스펙 의도는 '>1000 거부'이므로 400/413/422 중 하나면 통과."""
    with TestClient(app) as cli:
        r = cli.post("/api/v1/classify/batch", headers=HDR, json=_docs(1001))
        assert r.status_code in (400, 413, 422), r.text

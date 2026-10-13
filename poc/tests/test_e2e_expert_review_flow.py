"""Installed-review E2E tool defaults must follow the 20 reviewers x 50 documents plan."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "e2e_expert_review_flow.py"


def _load():
    spec = importlib.util.spec_from_file_location("expert_review_e2e", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self) -> dict:
        return self._payload


class _Requests:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url: str, **kwargs) -> _Response:
        self.calls.append(("GET", url, kwargs))
        params = kwargs.get("params") or {}
        headers = kwargs.get("headers") or {}
        if url.endswith("/candidates") and headers.get("Authorization") == "Bearer reviewer":
            if params.get("grade"):
                return _Response(422)
            return _Response(200, {
                "candidates": [{"doc_id": "RV-123", "title": "검수 문서"}],
                "summary": {"total": 50},
            })
        if url.endswith("/candidates/RV-123"):
            return _Response(200, {"doc_id": "RV-123", "text": "본문 " * 50})
        if url.endswith("/candidates/summary"):
            return _Response(200, {"total": 50})
        if url.endswith("/candidates") and headers.get("Authorization") == "Bearer admin":
            assert params == {"limit": 5, "review_batch": "expert_review_product_1000_20261010_rf"}
            return _Response(200, {
                "total": 1000,
                "candidates": [{"doc_id": "RF-0001", "proposed_grade": "S1"}],
            })
        if url.endswith("/assignments"):
            return _Response(200 if headers.get("Authorization") == "Bearer admin" else 403)
        raise AssertionError((url, kwargs))

    def post(self, url: str, **kwargs) -> _Response:
        self.calls.append(("POST", url, kwargs))
        if kwargs.get("headers", {}).get("Authorization") == "Bearer reviewer":
            assert kwargs["json"] == {"action": "approve"}
            return _Response(403)
        assert kwargs.get("headers") is None
        return _Response(401)


def test_read_only_defaults_verify_assigned_50_and_admin_batch_1000(monkeypatch):
    module = _load()
    fake = _Requests()
    monkeypatch.setattr(module, "requests", fake)
    monkeypatch.setattr(sys, "argv", [
        "e2e_expert_review_flow.py", "--reviewer-token", "reviewer", "--admin-token", "admin",
    ])

    assert module.main() == 0
    writes = [call for call in fake.calls if call[0] == "POST"]
    assert len(writes) == 2  # reviewer approve is rejected, anonymous write is rejected
    assert not any(call[2].get("json", {}).get("action") == "change" for call in writes)


def test_real_id_pattern_covers_current_rf_prefix():
    module = _load()
    assert module.REAL_ID.search("MS1-0001")
    assert module.REAL_ID.search("RF-0001")
    assert module.REAL_ID.search("MD-0001")
    assert not module.REAL_ID.search("RV-123")

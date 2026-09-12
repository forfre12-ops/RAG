"""GET /classify/{doc_id} — 확정 등급 회수 (KL 연동).

왜 필요한가(실측 2026-08-26, 시험 서버). confirm 은 예측 등급을 덮어쓰지 않는다. 모델이
무엇이라 했는지와 사람이 무엇으로 정했는지를 둘 다 남기는 설계이고, 사람 판단은
tb_corrections 에 적힌다. 그래서 예측 S2 인 문서를 S1 으로 확정한 뒤 조회해도 label 은
S2 로 돌아왔다 — KL 이 확정 등급을 받으려면 승격 대기 목록을 우회 조회해야 했고, 그 목록은
승격되면 사라지므로 신뢰할 수 없다.

이 테스트는 조회 응답이 확정 등급을 함께 내려 주는 것을 잠근다:
  · 교정 기록이 있으면  confirmed_label/by/at 가 채워지고 label 은 예측 그대로 남는다
  · 교정 기록이 없으면  세 필드가 None (기존 응답과 동일 — 추가 전용·하위호환)

⚠ 조회는 **문서** 기준이다. 교정은 확정 당시의 분류 행에 붙는데 같은 문서를 다시
분류하면 교정 없는 새 행이 생긴다. 분류 행 기준으로 찾으면 재분류 직후 확정 등급이
사라진 것처럼 보인다(실측 2026-08-26, 배포 검증에서 발견).
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from koipa.api import async_classify as ac
from koipa.api.app import app


def _hdr():
    from koipa.config import settings
    return {"X-API-Key": settings.api_key or "test-key"}


_DOC = uuid.uuid4()
_CLS = uuid.uuid4()
_CORRECTED_AT = dt.datetime(2026, 8, 26, 2, 24, 5, tzinfo=dt.timezone.utc)

# level_id → level_code (예측 S2 · 확정 S1)
_LEVELS = {2: "S2", 1: "S1"}


class _FakeDB:
    def get(self, model, pk):
        code = _LEVELS.get(pk)
        return SimpleNamespace(level_code=code) if code else None


class _FakeRepo:
    """list_recent_for_doc 은 분류 1건, latest_correction... 은 주입값을 돌려준다."""

    correction = None

    def __init__(self, db):
        self.db = db

    def list_recent_for_doc(self, doc_uuid, limit=1):
        return [
            SimpleNamespace(
                classification_id=_CLS,
                predicted_level_id=2,           # 예측 S2
                confidence=0.83,
                alternatives=[],
                model_version="v-test",
                inference_ms=236,
                status="confirmed",
            )
        ]

    def latest_correction_for_doc(self, doc_id):
        assert doc_id == _DOC
        return type(self).correction


def _patch(monkeypatch, correction):
    _FakeRepo.correction = correction

    @contextmanager
    def _scope():
        yield _FakeDB()

    monkeypatch.setattr(ac, "session_scope", _scope)
    monkeypatch.setattr(ac, "ClassifyRepo", _FakeRepo)


def test_confirmed_label_is_returned_alongside_prediction(monkeypatch):
    """확정이 있으면 확정 등급을 함께 준다 — 예측(label)은 덮지 않는다."""
    _patch(
        monkeypatch,
        SimpleNamespace(
            corrected_level_id=1,               # 확정 S1
            corrected_by="reviewer-01",
            corrected_at=_CORRECTED_AT,
        ),
    )
    res = TestClient(app).get(f"/api/v1/classify/{_DOC}", headers=_hdr())
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["label"] == "S2", "예측 등급은 감사 증적으로 보존되어야 한다"
    assert body["confirmed_label"] == "S1", "확정 등급이 응답에 있어야 한다"
    assert body["confirmed_by"] == "reviewer-01"
    assert body["confirmed_at"].startswith("2026-08-26")
    assert body["status"] == "confirmed"


def test_no_correction_leaves_fields_none(monkeypatch):
    """교정이 없으면 세 필드는 None — 기존 응답과 같다(하위호환)."""
    _patch(monkeypatch, None)
    res = TestClient(app).get(f"/api/v1/classify/{_DOC}", headers=_hdr())
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["label"] == "S2"
    assert body["confirmed_label"] is None
    assert body["confirmed_by"] is None
    assert body["confirmed_at"] is None

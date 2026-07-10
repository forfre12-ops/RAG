"""태깅 키워드 관리 CRUD — /admin/keywords (FUN-023).

PG 불요: 순수 헬퍼 + fake session + best-effort(DB 미가용) + 엔드포인트 매핑.
reload_rules 만 실 ClassifyService 싱글턴으로 통합 검증(모델 미로드 rule-fallback = 경량).
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from lloydk.db.models import ClassificationLevel, EvaluationFactor, LevelKeyword
from lloydk.schemas.common import Actor
from lloydk.schemas.keyword_admin import KeywordCreateRequest, KeywordUpdateRequest
from lloydk.services import keyword_admin_service as kas
from lloydk.services.keyword_admin_service import (
    KeywordAdminError,
    KeywordAdminService,
    resolve_factor_id,
    resolve_grade_id,
    to_item,
)


# ── 순수 헬퍼 ────────────────────────────────────────────────────────────────
def test_resolve_grade_id_ok():
    assert resolve_grade_id({"TS": 1, "S1": 2}, "S1") == 2


def test_resolve_grade_id_unknown_raises_400():
    with pytest.raises(KeywordAdminError) as ei:
        resolve_grade_id({"TS": 1}, "S9")
    assert ei.value.status_code == 400


def test_resolve_factor_id_none_when_absent():
    assert resolve_factor_id({"VALUE": 11}, None) == (None, [])


def test_resolve_factor_id_legacy_alias_maps_to_canonical():
    # NON_PUBLICITY(레거시) → SECRECY(정본) 로 해석.
    fid, warns = resolve_factor_id({"SECRECY": 10, "VALUE": 11}, "NON_PUBLICITY")
    assert fid == 10 and warns == []


def test_resolve_factor_id_direct_canonical():
    fid, warns = resolve_factor_id({"VALUE": 11}, "VALUE")
    assert fid == 11 and warns == []


def test_resolve_factor_id_unknown_is_null_plus_warning():
    fid, warns = resolve_factor_id({"VALUE": 11}, "BOGUS")
    assert fid is None and warns and "unknown factor" in warns[0]


def test_to_item_maps_fields():
    kw = SimpleNamespace(
        keyword_id=7, keyword="극비", pattern_type="exact", weight=0.9, is_active=True
    )
    item = to_item(kw, "TS", "VALUE")
    assert item.keyword_id == 7 and item.grade == "TS" and item.factor == "VALUE"
    assert item.weight == 0.9 and item.is_active is True


# ── fake session 인프라 ───────────────────────────────────────────────────────
class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def order_by(self, *a):
        return self

    def offset(self, n):
        return self

    def limit(self, n):
        return self

    def all(self):
        return list(self._rows)

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, levels, factors, keywords):
        self._data = {
            ClassificationLevel: levels,
            EvaluationFactor: factors,
            LevelKeyword: keywords,
        }
        self.added: list = []

    def query(self, model):
        return _FakeQuery(self._data.get(model, []))

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for i, obj in enumerate(self.added, start=1):
            if getattr(obj, "keyword_id", None) is None:
                obj.keyword_id = 1000 + i


def _install_fake(monkeypatch, session):
    @contextmanager
    def _scope():
        yield session

    monkeypatch.setattr(kas, "session_scope", lambda: _scope())
    # 서빙 리로드는 실 싱글턴 빌드를 피해 스텁(변경 로직만 검증).
    monkeypatch.setattr(
        KeywordAdminService, "_reload_serving", staticmethod(lambda: (True, 42))
    )


def _levels():
    return [
        SimpleNamespace(level_id=1, level_code="TS", is_active=True),
        SimpleNamespace(level_id=2, level_code="S1", is_active=True),
    ]


def _factors():
    return [
        SimpleNamespace(factor_id=10, factor_code="SECRECY", is_active=True),
        SimpleNamespace(factor_id=11, factor_code="VALUE", is_active=True),
    ]


# ── create ───────────────────────────────────────────────────────────────────
def test_create_happy_path_maps_ids_and_reloads(monkeypatch):
    sess = _FakeSession(_levels(), _factors(), [])
    _install_fake(monkeypatch, sess)
    req = KeywordCreateRequest(
        grade="S1", keyword="  자금계획  ", pattern_type="exact",
        factor="NON_PUBLICITY", weight=0.8, actor=Actor(user_id="admin1", role="admin"),
    )
    resp = KeywordAdminService().create(req)
    assert resp.action == "created"
    assert resp.serving_reloaded is True and resp.seed_count == 42
    # ORM 객체 필드 매핑: 등급 S1→level_id 2, 레거시 factor NON_PUBLICITY→SECRECY(10), trim.
    added = sess.added[0]
    assert added.level_id == 2 and added.factor_id == 10
    assert added.keyword == "자금계획" and added.source.startswith("admin:")
    assert resp.item.grade == "S1" and resp.item.keyword == "자금계획"


def test_create_bad_grade_raises_400(monkeypatch):
    sess = _FakeSession(_levels(), _factors(), [])
    _install_fake(monkeypatch, sess)
    req = KeywordCreateRequest(grade="S9", keyword="x", actor=Actor(user_id="a", role="admin"))
    with pytest.raises(KeywordAdminError) as ei:
        KeywordAdminService().create(req)
    assert ei.value.status_code == 400


def test_create_db_unavailable_raises_503(monkeypatch):
    def _boom():
        raise SQLAlchemyError("db down")

    monkeypatch.setattr(kas, "session_scope", _boom)
    req = KeywordCreateRequest(grade="TS", keyword="x", actor=Actor(user_id="a", role="admin"))
    with pytest.raises(KeywordAdminError) as ei:
        KeywordAdminService().create(req)
    assert ei.value.status_code == 503


# ── update / deactivate ──────────────────────────────────────────────────────
def test_update_deactivate_sets_inactive(monkeypatch):
    target = LevelKeyword(
        level_id=1, keyword="극비", pattern_type="exact", factor_id=11,
        weight=0.9, is_active=True,
    )
    target.keyword_id = 55
    sess = _FakeSession(_levels(), _factors(), [target])
    _install_fake(monkeypatch, sess)
    resp = KeywordAdminService().update(
        55, KeywordUpdateRequest(is_active=False, actor=Actor(user_id="a", role="admin"))
    )
    assert resp.action == "deactivated"
    assert target.is_active is False
    assert resp.item.is_active is False


def test_update_fields_partial(monkeypatch):
    target = LevelKeyword(
        level_id=1, keyword="old", pattern_type="exact", factor_id=None,
        weight=0.5, is_active=True,
    )
    target.keyword_id = 56
    sess = _FakeSession(_levels(), _factors(), [target])
    _install_fake(monkeypatch, sess)
    resp = KeywordAdminService().update(
        56,
        KeywordUpdateRequest(
            keyword="new", weight=0.7, factor="VALUE",
            actor=Actor(user_id="a", role="admin"),
        ),
    )
    assert resp.action == "updated"
    assert target.keyword == "new" and float(target.weight) == 0.7 and target.factor_id == 11


def test_update_not_found_raises_404(monkeypatch):
    sess = _FakeSession(_levels(), _factors(), [])  # 대상 없음
    _install_fake(monkeypatch, sess)
    with pytest.raises(KeywordAdminError) as ei:
        KeywordAdminService().update(
            999, KeywordUpdateRequest(weight=0.1, actor=Actor(user_id="a", role="admin"))
        )
    assert ei.value.status_code == 404


# ── list best-effort ─────────────────────────────────────────────────────────
def test_list_db_unavailable_is_best_effort(monkeypatch):
    def _boom():
        raise SQLAlchemyError("db down")

    monkeypatch.setattr(kas, "session_scope", _boom)
    resp = KeywordAdminService().list()
    assert resp.keywords == [] and resp.count == 0
    assert resp.warnings and "unavailable" in resp.warnings[0]


# ── reload_rules 통합 (실 싱글턴, DB 없이 KEYWORD_SEEDS 폴백) ───────────────────
def test_reload_rules_rebuilds_engine_from_seeds():
    from lloydk.services.classify_service import ClassifyService

    info = ClassifyService.get_instance().reload_rules()
    assert info["reloaded"] is True
    # DB 미가용이어도 build_rule_engine_from_db 가 KEYWORD_SEEDS 로 폴백 → 시드 수 > 0.
    assert info["seed_count"] > 0


# ── 엔드포인트 매핑 (핸들러 직접 호출) ────────────────────────────────────────
def test_endpoint_create_maps_service(monkeypatch):
    from lloydk.api import keyword_admin as kadmin
    from lloydk.schemas.keyword_admin import KeywordItem, KeywordMutationResponse

    sample = KeywordMutationResponse(
        keyword_id=1, action="created",
        item=KeywordItem(keyword_id=1, grade="TS", keyword="극비",
                         pattern_type="exact", factor="VALUE", weight=1.0, is_active=True),
        serving_reloaded=True, seed_count=10,
    )
    monkeypatch.setattr(KeywordAdminService, "create", lambda self, req: sample)
    req = KeywordCreateRequest(grade="TS", keyword="극비", actor=Actor(user_id="a", role="admin"))
    resp = kadmin.create_keyword(req)
    assert resp.keyword_id == 1 and resp.action == "created"


def test_endpoint_create_translates_error_to_http(monkeypatch):
    from fastapi import HTTPException

    from lloydk.api import keyword_admin as kadmin

    def _raise(self, req):
        raise KeywordAdminError(400, "bad grade")

    monkeypatch.setattr(KeywordAdminService, "create", _raise)
    req = KeywordCreateRequest(grade="S9", keyword="x", actor=Actor(user_id="a", role="admin"))
    with pytest.raises(HTTPException) as ei:
        kadmin.create_keyword(req)
    assert ei.value.status_code == 400

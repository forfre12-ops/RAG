"""태깅 키워드 관리 CRUD — /admin/keywords (FUN-023).

PG 불요: 순수 헬퍼 + fake session + best-effort(DB 미가용) + 엔드포인트 매핑.
reload_rules 만 실 ClassifyService 싱글턴으로 통합 검증(모델 미로드 rule-fallback = 경량).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from koipa.db.models import ClassificationLevel, EvaluationFactor, LevelKeyword
from koipa.schemas.common import Actor
from koipa.schemas.keyword_admin import KeywordCreateRequest, KeywordUpdateRequest
from koipa.services import keyword_admin_service as kas
from koipa.services.keyword_admin_service import (
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

    def first(self):
        return self._rows[0] if self._rows else None

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
    # 빈 DB라 코드 시드가 먼저 승격되므로(시드 절벽 가드) 신규 행은 마지막에 추가된다.
    added = sess.added[-1]
    assert added.level_id == 2 and added.factor_id == 10
    assert added.keyword == "자금계획" and added.source.startswith("admin:")
    assert resp.item.grade == "S1" and resp.item.keyword == "자금계획"


# ── 시드 절벽 가드 (룰엔진 404건 → 1건 붕괴 차단) ──────────────────────────────
def test_create_on_empty_db_promotes_code_seeds_first(monkeypatch):
    """빈 tb_level_keywords 에 첫 키워드를 넣으면 코드 시드가 먼저 DB로 승격돼야 한다.

    승격이 없으면 build_rule_engine_from_db 가 'DB 비어있지 않음'으로 판단해 DB의 1건만
    정본으로 삼고 KEYWORD_SEEDS 404건을 통째로 버린다 = 룰 경로 고등급 미탐 급증.
    """
    from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS

    sess = _FakeSession(_levels(), _factors(), [])  # DB 비어 있음
    _install_fake(monkeypatch, sess)
    req = KeywordCreateRequest(
        grade="TS", keyword="신규기밀어", actor=Actor(user_id="admin1", role="admin")
    )
    resp = KeywordAdminService().create(req)

    # 활성 등급(TS·S1)에 해당하는 코드 시드가 승격된 뒤 신규 1건이 추가된다.
    expected_seeds = sum(1 for s in KEYWORD_SEEDS if s["grade"] in {"TS", "S1"})
    assert expected_seeds > 0
    assert len(sess.added) == expected_seeds + 1
    assert sum(1 for o in sess.added if o.source == "seed_v1") == expected_seeds
    assert sess.added[-1].keyword == "신규기밀어"
    assert "code seeds promoted" in resp.note


def test_create_on_populated_db_does_not_reseed(monkeypatch):
    existing = LevelKeyword(
        level_id=1, keyword="극비", pattern_type="exact", factor_id=11,
        weight=0.9, is_active=True,
    )
    existing.keyword_id = 1
    sess = _FakeSession(_levels(), _factors(), [existing])
    _install_fake(monkeypatch, sess)
    resp = KeywordAdminService().create(
        KeywordCreateRequest(grade="TS", keyword="추가어", actor=Actor(user_id="a", role="admin"))
    )
    assert len(sess.added) == 1  # 신규 1건만, 재시드 없음(멱등)
    assert "code seeds promoted" not in resp.note


def test_list_on_empty_db_warns_code_seed_fallback(monkeypatch):
    sess = _FakeSession(_levels(), _factors(), [])
    _install_fake(monkeypatch, sess)
    resp = KeywordAdminService().list()
    assert resp.count == 0
    # '0건'을 '규칙 없음'으로 오독하지 않도록 폴백 상태를 명시해야 한다.
    assert resp.warnings and "code seeds" in resp.warnings[0]


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
    from koipa.services.classify_service import ClassifyService

    info = ClassifyService.get_instance().reload_rules()
    assert info["reloaded"] is True
    # DB 미가용이어도 build_rule_engine_from_db 가 KEYWORD_SEEDS 로 폴백 → 시드 수 > 0.
    assert info["seed_count"] > 0


# ── 엔드포인트 매핑 (핸들러 직접 호출) ────────────────────────────────────────
def test_endpoint_create_maps_service(monkeypatch):
    from koipa.api import keyword_admin as kadmin
    from koipa.schemas.keyword_admin import KeywordItem, KeywordMutationResponse

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

    from koipa.api import keyword_admin as kadmin

    def _raise(self, req):
        raise KeywordAdminError(400, "bad grade")

    monkeypatch.setattr(KeywordAdminService, "create", _raise)
    req = KeywordCreateRequest(grade="S9", keyword="x", actor=Actor(user_id="a", role="admin"))
    with pytest.raises(HTTPException) as ei:
        kadmin.create_keyword(req)
    assert ei.value.status_code == 400


# ── 실제 등급 판정에 미치는 영향 (LabelRuleEngine 통합, PG 불요) ────────────────────
# 위 CRUD 테스트들은 전부 fake session + 스텁 reload라 "키워드를 추가하면 실제 분류
# 결과가 바뀌는가"는 커버하지 않는다(2026-09-30). load_seeds_from_db()가
# KeywordAdminService.create()가 저장한 LevelKeyword를 KEYWORD_SEEDS와 동일한 형태
# (keyword/grade/factor/weight/pattern_type)로 내놓는다는 계약(seeds.py) 위에서,
# 그 형태의 시드가 LabelRuleEngine을 통해 실제로 등급을 바꾸는지 DB 없이 재현한다.
def test_admin_added_keyword_changes_classification_outcome():
    """관리자 콘솔에서 키워드를 추가하면 실제 등급 판정이 바뀌고, 빼면 원복되는가."""
    from koipa.config import settings
    from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine
    from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS

    text = (
        "본 문서는 2026년도 상반기 사업 추진 현황을 정리한 일반 업무 보고서입니다. "
        "특이사항 없이 정상적으로 진행되고 있습니다. "
        "존재하지않는테스트전용키워드가 포함되어 있습니다."
    )

    baseline = LabelRuleEngine(seeds=KEYWORD_SEEDS).label(text)
    assert baseline.grade_scores.get("S1", 0.0) == 0.0
    assert baseline.grade != "S1"

    # KeywordAdminService.create()가 DB에 쌓는 LevelKeyword 한 행과 같은 모양.
    admin_added_seed = {
        "keyword": "존재하지않는테스트전용키워드",
        "grade": "S1",
        "factor": "SECRECY",
        "weight": 3.0,
        "pattern_type": "exact",
    }
    with_keyword = LabelRuleEngine(seeds=[*KEYWORD_SEEDS, admin_added_seed]).label(text)
    s1_score = with_keyword.grade_scores.get("S1", 0.0)
    assert s1_score == 3.0
    assert with_keyword.grade == "S1"
    # m5_inference/pipeline.py의 FNR-safe override가 실제로 비교하는 바로 그 임계값 —
    # 이 임계를 넘어야 모델 예측과 무관하게 최종등급이 강제로 올라간다.
    assert s1_score >= settings.fnr_rule_s1_threshold

    # 비활성화(is_active=False)하면 load_seeds_from_db()가 그 행을 제외한다(seeds.py의
    # LevelKeyword.is_active 필터) — 키워드 없는 시드 목록으로 돌아가는 것과 동일 효과.
    reverted = LabelRuleEngine(seeds=KEYWORD_SEEDS).label(text)
    assert reverted.grade_scores == baseline.grade_scores
    assert reverted.grade == baseline.grade


def _pg_up() -> bool:
    """판정은 _pg_probe 한 곳에만 둔다 — DATABASE_URL의 host·port를 본다(repo 관례)."""
    from _pg_probe import postgres_available

    return postgres_available()


@pytest.mark.skipif(not _pg_up(), reason="postgres not available (DATABASE_URL 기준)")
def test_load_seeds_from_db_excludes_inactive_keyword():
    """/admin/keywords가 실제로 쓰는 테이블에서, 비활성 처리한 키워드가 룰엔진 시드
    로딩(load_seeds_from_db)에서 실제로 빠지는가 — 실 DB. 테스트가 남긴 행은 끝에 지운다.
    """
    from koipa.db import session_scope
    from koipa.db.models import ClassificationLevel, LevelKeyword
    from koipa.modules.m3_labeling.seeds import load_seeds_from_db

    marker = "존재하지않는테스트전용키워드_pg_" + uuid.uuid4().hex[:8]
    kid = None
    try:
        with session_scope() as db:
            level = (
                db.query(ClassificationLevel)
                .filter(ClassificationLevel.is_active.is_(True))
                .first()
            )
            assert level is not None, "활성 등급이 하나도 없습니다 — 등급체계 시드 확인 필요"
            kw = LevelKeyword(
                level_id=level.level_id, keyword=marker, pattern_type="exact",
                factor_id=None, weight=1.0, source="test", is_active=True,
            )
            db.add(kw)
            db.flush()
            kid = kw.keyword_id

        seeds = load_seeds_from_db()
        assert seeds is not None
        assert any(s["keyword"] == marker for s in seeds), "활성 키워드가 시드 로딩에 없습니다"

        with session_scope() as db:
            row = db.query(LevelKeyword).filter(LevelKeyword.keyword_id == kid).one()
            row.is_active = False

        seeds_after = load_seeds_from_db()
        assert seeds_after is None or not any(s["keyword"] == marker for s in seeds_after), (
            "비활성화했는데도 시드 로딩에 남아 있습니다 — load_seeds_from_db의 is_active 필터가 깨졌습니다"
        )
    finally:
        if kid is not None:
            with session_scope() as db:
                db.query(LevelKeyword).filter(LevelKeyword.keyword_id == kid).delete()

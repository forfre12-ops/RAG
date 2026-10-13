"""미탐 지표를 재는 조건은 'DB 등록' 이 아니라 '서빙이 무엇을 적재했나' 다.

왜 이 시험이 있는가(2026-09-12). 211 에서 `S9.2(적대적 FNR)` · `S9.4(무음 미탐)` ·
`S1.3(F1)` · `S1.4(FNR)` 이 매 회차 `missing: trained_model` 로 SKIP 됐다. RFP 가 스스로
"핵심 성능 목표 = 미탐 최소화"(FUN-024)라고 적은 지표가 **서버에서 한 번도 안 재진 것**이다.

원인은 모델 파일이 아니라 DB 였다 — `_detect_trained_model()` 이 **활성 ModelVersion 행**만
보는데 211 의 `tad_mm_mdl_ver_mng` 는 0행이고(2026-09-10 PostgreSQL 재배포 때 안 옮겼다),
서버는 `CLASSIFIER_MODEL_DIR` 폴백으로 서빙 중이라 healthz 는 `model=loaded` 다.
KPI 가 재는 대상은 **서빙 경로**이므로 서빙이 무엇을 적재했는지가 옳은 질문이다.

경계 둘을 함께 고정한다.
- dryrun(룰 폴백)에서는 여전히 False 여야 한다 — 그렇지 않으면 룰 엔진 출력으로 F1 을 보고한다.
- 자원 판정이 모델을 **적재시키면 안 된다**. 하니스는 시나리오보다 먼저 자원을 판정하므로,
  거기서 모델을 올리면 S11 측정 구간에 적재 비용이 섞인다(`--only S11` 단독이 CPU 97% 로
  찍혔던 것과 같은 함정 — memory: rfp-performance-requirement-no-accuracy-target).
"""

from __future__ import annotations

import sys
import types

from koipa.perf.harness import (
    AvailableResources,
    ScenarioRunner,
    ScenarioSpec,
    _detect_trained_model,
    _serving_model_loaded,
)

_MOD = "koipa.services.classify_service"


def _install_fake_service(monkeypatch, *, model) -> None:
    """이미 만들어진 ClassifyService 인스턴스가 있는 상태를 흉내낸다."""
    mod = types.ModuleType(_MOD)

    class _Inference:
        _model = model

    class ClassifyService:
        _instance = None

    ClassifyService._instance = types.SimpleNamespace(inference=_Inference())
    mod.ClassifyService = ClassifyService
    monkeypatch.setitem(sys.modules, _MOD, mod)


# ----------------------------------------------------------------
# 서빙 적재 감지
# ----------------------------------------------------------------


def test_module_not_imported_is_false(monkeypatch):
    """분류 서비스가 아직 임포트되지도 않았으면 False — 임포트를 유발하지 않는다."""
    monkeypatch.delitem(sys.modules, _MOD, raising=False)
    assert _serving_model_loaded() is False


def test_no_instance_is_false(monkeypatch):
    """모듈은 있어도 인스턴스가 없으면 False — 자원 판정이 모델을 적재시키면 안 된다."""
    mod = types.ModuleType(_MOD)

    class ClassifyService:
        _instance = None

    mod.ClassifyService = ClassifyService
    monkeypatch.setitem(sys.modules, _MOD, mod)
    assert _serving_model_loaded() is False


def test_instance_without_model_is_false(monkeypatch):
    """인스턴스는 있는데 룰 폴백(_model None)이면 False — dryrun 이 여기 해당한다."""
    _install_fake_service(monkeypatch, model=None)
    assert _serving_model_loaded() is False


def test_instance_with_model_is_true(monkeypatch):
    _install_fake_service(monkeypatch, model=object())
    assert _serving_model_loaded() is True


# ----------------------------------------------------------------
# 자원 판정 우선순위: env > 서빙 > DB
# ----------------------------------------------------------------


def test_serving_model_makes_resource_available_without_db_row(monkeypatch):
    """211 의 상황 — 모델 버전 표가 0행이어도 서빙이 적재했으면 잴 수 있다."""
    monkeypatch.delenv("KOIPA_TRAINED_MODEL", raising=False)
    _install_fake_service(monkeypatch, model=object())
    assert _detect_trained_model() is True


def test_env_flag_still_wins_over_serving(monkeypatch):
    """명시적으로 끈 환경(CI 등)에서는 서빙이 적재했어도 False — 강제 플래그가 우선이다."""
    monkeypatch.setenv("KOIPA_TRAINED_MODEL", "0")
    _install_fake_service(monkeypatch, model=object())
    assert _detect_trained_model() is False


def test_rule_fallback_keeps_kpis_skipped(monkeypatch):
    """서빙이 룰 폴백이면 여전히 False — 룰 출력으로 F1·FNR 을 보고하면 안 된다."""
    monkeypatch.delenv("KOIPA_TRAINED_MODEL", raising=False)
    _install_fake_service(monkeypatch, model=None)
    assert _detect_trained_model() is False


# ----------------------------------------------------------------
# 숫자 아닌 근거(평가셋 출처)를 결과에 남긴다
# ----------------------------------------------------------------


def test_notes_reach_the_result_and_json():
    """`record()` 는 float|bool 만 받는다 — "무엇으로 쟀나" 는 숫자가 아니라 로그에만 있었다."""
    def runner(ctx):
        ctx.note("eval_set", "holdout_eval.hardened.jsonl")
        ctx.note("eval_n", "42")
        ctx.record("s1_1", 12.0)

    results = ScenarioRunner(mode="dryrun", resources=AvailableResources(), kpis=[]).run(
        [ScenarioSpec(id="S1", title="시험용", runner=runner)]
    )
    assert results[0].notes == {"eval_set": "holdout_eval.hardened.jsonl", "eval_n": "42"}
    # 보고서·다음 사람이 보는 것은 JSON 이다 — 거기까지 실려야 의미가 있다.
    assert results[0].to_dict()["notes"]["eval_set"] == "holdout_eval.hardened.jsonl"


def test_notes_do_not_become_kpi_measurements():
    """근거는 판정에 쓰이지 않는다 — measurements 를 오염시키면 KPI 줄이 생긴다."""
    def runner(ctx):
        ctx.note("eval_set", "x.jsonl")

    results = ScenarioRunner(mode="dryrun", resources=AvailableResources(), kpis=[]).run(
        [ScenarioSpec(id="S1", title="시험용", runner=runner)]
    )
    assert results[0].measurements == {}

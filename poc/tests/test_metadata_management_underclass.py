"""확인된 비밀관리성(M)이 예측 등급과 어긋날 때 — 무음으로 자동확정되지 않는가.

왜 이 시험이 있는가(실측 2026-09-09, v-fe4b386b · METADATA_FLOOR_ENABLED=true).
같은 본문에 메타데이터만 갈아 끼우며 배포본을 태워 봤다.

    access_scope=designated    → 요소 (2,2,1) · 등급 S1 · status=staging
    access_scope=approved_only → 요소 (2,2,2) · 등급 S1 · status=staging

정본 공식에서 S1 이 나오는 조합은 **(2,2,0) 하나뿐**이다. 즉 M 이 확인된 순간 그 문서는
S1 일 수 없는데, 화면에는 (2,2,2)·S1 이 함께 떴고 아무 신호 없이 자동확정 대상이었다.
표시 요소는 M 만 실측이고 S·V 는 등급에서 역산한 값이라(svm_levels_for_grade) 둘을 한
벡터로 내보내면 이런 조합이 만들어진다.

이 시험이 지키는 것은 하나다 — **등급을 바꾸지 말고, 자동확정만 막을 것.**
하향은 미탐을 늘리므로 하지 않는다. 방향이 반대(과대)인 경우는 표시만 하고 검수로 보내지
않는다. 그것까지 검수로 보내면 검수 부담만 늘고 미탐은 안 준다.
"""

from __future__ import annotations

import pytest

from koipa.modules.m3_labeling.rule_engine import grade_from_svm, svm_levels_for_grade


@pytest.mark.parametrize("grade", ["TS", "S1", "S2", "S3"])
def test_reconstructed_factors_agree_with_the_formula(grade):
    """대조의 전제 — M 을 손대기 전의 역산 요소는 등급과 일치한다.

    이것이 깨지면 아래 대조는 M 이 아니라 역산표의 불일치를 잡게 된다.
    """
    assert grade_from_svm(*svm_levels_for_grade(grade)) == grade


def test_confirmed_management_makes_s1_impossible():
    """정본 공식이 말하는 것 — S1 은 M=0 에서만 나온다."""
    assert grade_from_svm(2, 2, 0) == "S1"
    for m in (1, 2):
        assert grade_from_svm(2, 2, m) == "TS", "M 이 확인되면 (2,2,M) 은 S1 일 수 없다"


@pytest.fixture
def _stub_pipeline_db(monkeypatch):
    """InferencePipeline 생성이 PG 에 매달리지 않게 스텁 — 기존 metadata_floor 시험과 동일."""
    from koipa.schemas import common as _common

    monkeypatch.setattr(
        _common.GradeRegistry, "get_codes", lambda *a, **k: ["TS", "S1", "S2", "S3"]
    )
    from koipa.modules.m3_labeling import pipeline as _m3

    monkeypatch.setattr(_m3, "build_rule_engine_from_db", lambda *a, **k: object())


def _run(monkeypatch, metadata, predicted="S1"):
    """예측 등급과 그 등급의 역산 요소를 고정한 채, 메타데이터만 바꿔 파이프라인을 태운다."""
    import koipa.config as cfg
    from koipa.modules.m5_inference.pipeline import InferencePipeline, InferenceResult
    from koipa.schemas.classify import EvaluationFactors
    from koipa.schemas.common import Grade

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    s, v, m = svm_levels_for_grade(predicted)
    forced = InferenceResult(
        label=Grade[predicted],
        confidence=0.9,
        scores={g: (0.9 if g == predicted else 0.0) for g in ("TS", "S1", "S2", "S3")},
        factors=EvaluationFactors.from_factor_scores(
            {"SECRECY": float(s), "VALUE": float(v), "MANAGEMENT": float(m)}
        ),
    )
    pipe = InferencePipeline()
    monkeypatch.setattr(pipe, "_run_rule_fallback", lambda *a, **k: forced)
    return pipe.run("내부 문서", metadata=metadata)


def test_confirmed_management_on_s1_is_not_silently_auto_confirmed(monkeypatch, _stub_pipeline_db):
    """실측 재현 — approved_only(M=2) 인데 예측 S1 이면 신호가 나야 한다."""
    out = _run(monkeypatch, {"access_scope": "approved_only"})
    joined = " ".join(out.warnings)
    assert "metadata-management-underclass" in joined, (
        "확인된 M 이 예측보다 높은 등급을 가리키는데 아무 신호도 없다 — 무음 미탐이다"
    )
    assert out.label.value == "S1", "등급은 바꾸지 않는다(등급 우선·요소 후행 구조)"


def test_overclass_direction_is_shown_but_not_routed(monkeypatch, _stub_pipeline_db):
    """방향이 반대면 표시만 한다 — 하향도, 검수 라우팅도 하지 않는다."""
    out = _run(monkeypatch, {"access_scope": "all_employees"}, predicted="S2")
    joined = " ".join(out.warnings)
    assert "metadata-management-overclass" in joined
    assert "metadata-management-underclass" not in joined
    assert out.label.value == "S2"


def test_agreeing_management_stays_quiet(monkeypatch, _stub_pipeline_db):
    """요소와 등급이 공식상 맞으면 아무 말도 하지 않는다 — 없는 충돌을 만들지 않는다."""
    out = _run(monkeypatch, {"access_scope": "all_employees"}, predicted="S1")
    joined = " ".join(out.warnings)
    assert "underclass" not in joined and "overclass" not in joined


def test_missing_metadata_stays_quiet(monkeypatch, _stub_pipeline_db):
    """메타데이터가 없으면 조용하다 — 실 트래픽 전량에 경고가 뜨면 신호가 죽는다."""
    out = _run(monkeypatch, {})
    joined = " ".join(out.warnings)
    assert "underclass" not in joined and "overclass" not in joined


def _classify(monkeypatch, metadata, predicted="S1"):
    """서비스 계층까지 태운다 — status 가 실제로 바뀌는지는 여기서만 보인다."""
    import uuid

    import koipa.config as cfg
    from koipa.modules.m5_inference.pipeline import InferenceResult
    from koipa.schemas.classify import ClassifyRequest, EvaluationFactors
    from koipa.schemas.common import Grade
    from koipa.services.classify_service import ClassifyService

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    s, v, m = svm_levels_for_grade(predicted)
    forced = InferenceResult(
        label=Grade[predicted],
        confidence=0.99,          # 임계(0.50)를 넉넉히 넘겨 low-confidence 라우팅과 분리한다
        scores={g: (0.99 if g == predicted else 0.0) for g in ("TS", "S1", "S2", "S3")},
        factors=EvaluationFactors.from_factor_scores(
            {"SECRECY": float(s), "VALUE": float(v), "MANAGEMENT": float(m)}
        ),
    )
    svc = ClassifyService()
    monkeypatch.setattr(svc.inference, "_run_rule_fallback", lambda *a, **k: forced)
    return svc.classify(
        ClassifyRequest(
            doc_id=str(uuid.uuid4()),
            content="사내 기술 자료 본문입니다. 공정 조건과 측정값을 정리했습니다.",
            metadata=metadata or None,
        )
    )


def test_underclass_signal_actually_routes_to_review(monkeypatch, _stub_pipeline_db):
    """신호가 서비스 계층에서 **실제로** 검수 라우팅에 걸리는가.

    2026-08-22 에 metadata-management-conflict 가 응답 텍스트에만 남고 라우팅에는 안 걸려
    있던 전례가 있다(TS/S1 문서에 all_employees 를 주입해도 staging 그대로였다). 신호를
    만드는 것과 그 신호가 쓰이는 것은 별개라, 경고 문자열이 아니라 status 를 본다.
    """
    res = _classify(monkeypatch, {"access_scope": "approved_only"})
    assert res.status == "needs_review", (
        f"확인된 M(=2)이 예측 S1 과 어긋나는데 자동확정됐다: status={res.status}"
    )
    assert res.label.value == "S1", "등급은 바꾸지 않는다 — 자동확정만 막는다"


def test_overclass_does_not_add_review_load(monkeypatch, _stub_pipeline_db):
    """과대 방향은 검수로 보내지 않는다 — 미탐을 안 줄이면서 검수 부담만 늘린다."""
    res = _classify(monkeypatch, {"access_scope": "all_employees"}, predicted="S2")
    assert res.status != "needs_review", f"과대 방향까지 검수로 보냈다: {res.warnings}"


def test_no_metadata_keeps_prior_behaviour(monkeypatch, _stub_pipeline_db):
    """메타데이터가 없으면 이전과 같다 — 실 데이터 전량이 이 경로다(M 보유 0건)."""
    res = _classify(monkeypatch, {})
    assert res.status != "needs_review"
    assert not any("underclass" in w or "overclass" in w for w in res.warnings)

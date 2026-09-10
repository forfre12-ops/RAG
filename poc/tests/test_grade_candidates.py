"""비밀관리성(M)을 못 받았을 때 등급을 하나로 단정하지 않는가.

왜 이 출력이 있는가(2026-09-10). 정본 공식에서 S1 이 나오는 조합은 **(2,2,0) 하나뿐**이라
S1 과 TS 를 가르는 것은 오직 M 이다. 그런데 M 공급이 0 건이다(전 데이터셋 432,820행 ·
`scripts/measure_management_input_gap.py`). 그 상태에서 단일 등급만 내보내는 것은
**없는 정보를 있는 척하는 것**이다.

    label            그대로 둔다 — 기존 계약을 깨지 않는다
    grade_candidates M 하나로 갈리는 등급들. 비면 갈릴 것이 없다는 뜻이다

검수자가 얻는 것은 "등급이 애매하다"가 아니라 **"확인할 것은 등급이 아니라 접근권한"**
이라는 방향이다. 그것이 이 출력의 값이다.

이 시험이 지키는 것 넷.
    ① M 을 못 받으면 후보가 나오는가
    ② M 을 받으면 후보가 **사라지는가** (안 사라지면 늘 켜진 경고와 같다)
    ③ label 이 바뀌지 않는가 (계약 보존)
    ④ 갈릴 것이 없으면 조용한가 (S3 처럼 M 과 무관한 구간)
"""

from __future__ import annotations

import uuid

import pytest

from koipa.modules.m3_labeling.rule_engine import grade_from_svm, svm_levels_for_grade

_BODY = "전극 코팅 공정 시험 결과. 경쟁사가 확보하지 못한 공정 노하우를 담고 있다."


@pytest.fixture
def _stub_pipeline_db(monkeypatch):
    from koipa.schemas import common as _common

    monkeypatch.setattr(
        _common.GradeRegistry, "get_codes", lambda *a, **k: ["TS", "S1", "S2", "S3"]
    )
    from koipa.modules.m3_labeling import pipeline as _m3

    monkeypatch.setattr(_m3, "build_rule_engine_from_db", lambda *a, **k: object())


def _classify(monkeypatch, predicted="S1", metadata=None, text=_BODY):
    import koipa.config as cfg
    from koipa.modules.m5_inference.pipeline import InferenceResult
    from koipa.schemas.classify import ClassifyRequest, EvaluationFactors
    from koipa.schemas.common import Grade
    from koipa.services.classify_service import ClassifyService

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    s, v, m = svm_levels_for_grade(predicted)
    forced = InferenceResult(
        label=Grade[predicted], confidence=0.99,
        scores={g: (0.99 if g == predicted else 0.0) for g in ("TS", "S1", "S2", "S3")},
        factors=EvaluationFactors.from_factor_scores(
            {"SECRECY": float(s), "VALUE": float(v), "MANAGEMENT": float(m)}
        ),
    )
    svc = ClassifyService()
    monkeypatch.setattr(svc.inference, "_run_rule_fallback", lambda *a, **k: forced)
    return svc.classify(
        ClassifyRequest(doc_id=str(uuid.uuid4()), content=text, metadata=metadata)
    )


def test_s1_prediction_without_m_is_not_asserted_alone(monkeypatch, _stub_pipeline_db):
    """S1 은 M=0 을 전제한 등급이다 — M 을 모르면 TS 일 수도 있다고 말해야 한다."""
    res = _classify(monkeypatch, predicted="S1")
    assert res.grade_candidates == ["TS", "S1"], res.grade_candidates
    assert res.label.value == "S1", "label 을 바꾸면 기존 계약이 깨진다"
    assert "접근범위" in (res.grade_candidates_reason or ""), (
        "무엇을 확인해야 하는지 말해 주지 않으면 검수자가 등급만 다시 본다"
    )


def test_candidates_disappear_once_m_is_known(monkeypatch, _stub_pipeline_db):
    """**늘 켜진 경고는 없는 것과 같다.** M 을 받으면 후보는 사라져야 한다."""
    res = _classify(monkeypatch, predicted="S1", metadata={"access_scope": "approved_only"})
    assert res.grade_candidates == [], f"M 을 받았는데도 후보가 남았다: {res.grade_candidates}"
    assert res.grade_candidates_reason is None


def test_document_stamp_also_settles_it(monkeypatch, _stub_pipeline_db):
    """머리말 도장으로 M 이 채워져도 마찬가지다 — 두 경로가 같은 결론을 낸다."""
    stamped = "[docx section 1 header]\n대외비\n[docx section 1 body]\n" + _BODY
    res = _classify(monkeypatch, predicted="S1", text=stamped)
    assert res.grade_candidates == []


def test_public_grade_has_nothing_to_split(monkeypatch, _stub_pipeline_db):
    """S3 는 어떤 M 에서도 S3 다 — 갈릴 것이 없으면 조용해야 한다."""
    assert {grade_from_svm(0, 0, m) for m in (0, 1, 2)} == {"S3"}, "전제가 깨졌다"
    res = _classify(monkeypatch, predicted="S3")
    assert res.grade_candidates == []


def test_s2_prediction_splits_with_s3(monkeypatch, _stub_pipeline_db):
    """S2 도 M 이 0 이면 S3 다 — S1/TS 만의 이야기가 아니다."""
    res = _classify(monkeypatch, predicted="S2")
    assert res.grade_candidates == ["S2", "S3"], res.grade_candidates


def test_candidates_are_ordered_most_severe_first(monkeypatch, _stub_pipeline_db):
    """순서가 뒤집히면 화면 맨 앞에 낮은 등급이 온다 — 미탐 방향으로 읽힌다."""
    from koipa.modules.m3_labeling.seeds import GRADE_ORDER

    res = _classify(monkeypatch, predicted="S1")
    ranks = [GRADE_ORDER[g] for g in res.grade_candidates]
    assert ranks == sorted(ranks), f"심각한 등급이 먼저 와야 한다: {res.grade_candidates}"


def test_candidates_always_contain_the_predicted_label(monkeypatch, _stub_pipeline_db):
    """예측이 후보에 없으면 두 출력이 서로를 부정한다."""
    for predicted in ("TS", "S1", "S2"):
        res = _classify(monkeypatch, predicted=predicted)
        if res.grade_candidates:
            assert res.label.value in res.grade_candidates, (
                f"{predicted} 예측이 후보 {res.grade_candidates} 에 없다"
            )

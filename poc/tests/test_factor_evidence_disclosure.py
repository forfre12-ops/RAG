"""S·V 요소값이 **근거인지 등급의 재진술인지** 응답이 정직하게 말하는지 고정한다.

왜(진단 2026-08-12 · 조치 2026-08-15). 룰 엔진에서 s_lv·v_lv 는 요소 근거가 아니라
`content_grade`(키워드 argmax 등급)에서 역산된다 — 한 줄이 둘을 동시에 정한다.

    strong = content_grade in ("TS", "S1")
    s_lv = 0 if (public or content_grade == "S3") else (2 if strong else 1)
    v_lv = 2 if strong else (0 if content_grade == "S3" else 1)

그런데 응답의 `factors_source` 는 그것을 `rule_evidenced` 라고 불렀다. **M 축에는 같은
공시가 이미 있었고 S·V 만 빠져 있었다** — 같은 방식으로 나온 값인데 하나만 밝혔다.

규모는 이미 측정돼 있다(RULE_EXTRACTOR_DIAGNOSIS §4).

    v3 final_800   secrecy 낮게봄 84.6% · value 낮게봄 84.6% · 과검출 0.0%
    누산 점수       VALUE 300건 전부 0.0 · SECRECY 전 문서 동일값 1.35
    경화 홀드아웃    룰 비-S3 판정 27건 중 26건(96.3%)이 V 근거 없음 (2026-08-15 실측)

⚠ 탐지 자체는 못 고친다. 시드 보강·semantic 매칭·임계 탐색 셋 다 막혔다(같은 문서 §7).
  semantic 은 코사인 0.30~0.53 으로 음성 대조군과 겹쳤고, VALUE 누산은 분산이 0 이라
  임계를 정할 대상이 없었다. 남은 정직한 조치는 **탐지 못 한 것을 탐지했다고 말하지
  않는 것**이고 이 파일이 그것을 고정한다.

⚠ 등급은 바뀌지 않는다. 순수 공시다 — 여기서 등급을 움직이면 판정면이 바뀐다.
"""
from __future__ import annotations

import pytest

from koipa.modules.m3_labeling.pipeline import LabelingPipeline
from koipa.modules.m3_labeling.rule_engine import build_rule_engine_from_db
from koipa.schemas.classify import EvaluationFactors, FactorDetail
from koipa.services.classify_service import ClassifyService

_DISCLOSURE = "독립 근거 없음"


@pytest.fixture(scope="module")
def engine():
    return build_rule_engine_from_db()


def test_result_carries_per_factor_evidence(engine):
    """S·V 도 M 처럼 근거 유무를 실어야 한다 — 안 실으면 응답이 판단할 수 없다."""
    r = engine.label("사내 자료입니다. 공개 가능 여부는 검토 중입니다.")
    for attr in ("secrecy_evidenced", "value_evidenced", "management_evidenced"):
        assert isinstance(getattr(r, attr), bool), f"{attr} 가 없다"


def test_non_s3_without_value_seed_discloses(engine):
    """비-S3 인데 VALUE 시드가 안 걸렸으면 공시해야 한다.

    실측: 경화 홀드아웃의 룰 비-S3 판정 27건 중 26건이 이 상태다.
    """
    hits = 0
    for text in (
        "당사 원가 구조 및 납품 단가 협상 전략. 대외 공개 불가.",
        "리콜 대응 계획 초안. 내부 자료.",
    ):
        r = engine.label(text)
        if r.grade != "S3" and not r.value_evidenced:
            assert any(_DISCLOSURE in w and "경제적유용성" in w for w in r.warnings), (
                f"V 근거 없음인데 공시가 없다: {r.warnings}"
            )
            hits += 1
    assert hits, "비-S3 표본이 안 나와 계약을 확인하지 못했다"


def test_s3_does_not_disclose(engine):
    """S3 에는 공시하지 않는다 — M 축과 같은 규율이다.

    S3 는 '영업비밀 아님' 이라 요소 근거를 주장하지 않는다. 여기서까지 경고를 내면
    모든 문서에 경고가 붙어 아무도 안 읽는다.
    """
    r = engine.label("점심 메뉴 안내입니다. 오늘은 김치찌개입니다.")
    assert r.grade == "S3"
    assert not [w for w in r.warnings if _DISCLOSURE in w]


def test_factors_source_reports_estimated_when_v_unevidenced(engine):
    """V축 근거가 없으면(공시 대상) factors_source 는 rule_evidenced 라고 말하면 안 된다.

    이것이 이번 수정의 핵심이다 — 역산값을 '룰 근거' 라고 부르던 것을 멈춘다.

    [2026-10-02] factors_source는 더는 경고 **문자열**을 안 본다 — EvaluationFactors의
    축별 state를 직접 본다(classify_service.py _factors_source). 종전 문자열 매칭은
    역산이 일어나는 4경로 중 2곳(출처사전 cap·청크 severe-agg 폴백)을 못 잡는 버그가
    있었다 — 축 state를 직접 보면 그 버그가 설계상 사라진다. LabelingPipeline을 통해
    실제 EvaluationFactors가 조립되는 경로까지 확인한다(단위 로직만이 아니라 배선까지).
    """
    pipeline = LabelingPipeline(rule_engine=engine)
    result = pipeline.label("리콜 대응 계획 초안. 내부 자료.")
    assert result.rule_result.grade != "S3"
    assert not result.rule_result.value_evidenced
    assert ClassifyService._factors_source(result.factors) == "model_estimated"


def test_factors_source_rule_evidenced_when_all_axes_observed():
    f = EvaluationFactors(
        secrecy=FactorDetail(state="observed", value=2, evidence=["근거"]),
        value=FactorDetail(state="observed", value=2, evidence=["근거"]),
        management=FactorDetail(state="observed", value=1, evidence=["근거"]),
    )
    assert ClassifyService._factors_source(f) == "rule_evidenced"


def test_disclosure_does_not_change_grade(engine):
    """공시는 등급을 건드리지 않는다 — 판정면 불변이 이 변경의 전제다."""
    text = "당사 원가 구조 및 납품 단가 협상 전략. 대외 공개 불가."
    before = engine.label(text)
    after = engine.label(text)
    assert before.grade == after.grade
    assert before.svm == after.svm

"""LLM 기반 라벨링 — 룰엔진 confidence가 낮거나 경계 사례에서 fallback.

프롬프트 전략: 4대 평가요소 점수화 → 종합 등급 결정.
LLM 응답은 JSON 형식 강제.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Mapping

from lloydk.adapters.llm import build_provider
from lloydk.adapters.llm.base import LLMProvider
from lloydk.modules.m3_labeling.consensus import GRADE_RANK

SYSTEM_PROMPT = """당신은 한국 영업비밀 등급분류 가이드(정본)에 정통한 분류 전문가다.
등급 = 비공지성(S) × 경제적 유용성(V) × 비밀관리성(M). 각 요소를 0·1·2점으로 매긴 뒤 곱한다.

[요소 점수 기준]
- 비공지성(S): 0=이미 공개된 정보 / 1=통상적 방법으론 입수 곤란 / 2=보유자를 통하지 않곤 알 수 없음
- 경제적 유용성(V): 0=경제적 가치 없음 / 1=취득·개발에 비용·노력 투입 / 2=상당한 비용·노력 투입
- 비밀관리성(M): 0=공개·관리 안 됨 / 1=임직원 일부만 공유 / 2=승인된 자에 한해 공개

[등급 결정 (정본 v2.2 — 단순 곱 크기 매핑 아님)]
- 비공지성 S=2 그리고 경제적유용성 V=2 인 경우:
    · 비밀관리성 M≥1 → TS(특급기밀)
    · M=0          → S1(1급 비밀)   ← 고가치 비밀이나 관리만 미공식화 (곱=0이어도 S3 아님)
- 그 외(S 또는 V 가 2 미만):
    · 곱(S×V×M) ≥ 1 → S2(2급 대외비)
    · 곱 = 0        → S3(3급 공개)

[중요]
- "외부 공개 시 유출 위험"처럼 *공개를 경고*하는 문장은 그 문서가 **비밀**이라는 뜻(공개 아님). 공개(S=0)는 보도자료·공시·공개특허처럼 *실제로 공표된* 경우만.
- 미탐(고등급을 저등급으로) 금지. 모호하면 한 단계 위.

[예시] (괄호=비공지·가치·관리 레벨)
- 중장기 경영계획(S2·V2·M2) → TS / 연구개발 보고서·핵심 공정 레시피 → TS~S1
- 고가치 비밀이나 관리 미공식화(S2·V2·M0) → S1 / 원가구조·자금계획·고객DB → S1
- 조직도(S1·V1·M1)·내부 검토안·파트너십 협상안 → S2
- 보도자료·채용공고·공시(S=0) → S3

출력(JSON only, 다른 텍스트 금지):
{"grade":"TS|S1|S2|S3","secrecy":0,"value":0,"management":0,"confidence":0.0,"rationale":"1문장"}
- confidence: 이 등급 판단의 확신도(0~1). 요소 점수가 명확하면 1에 가깝게, 모호하면 낮게."""


# This suffix is used only by the proxy-corpus judge. Keeping it out of the
# default prompt preserves the production/ordinary-golden labelling contract,
# while still obtaining grade and document-quality evidence in one model call.
PROXY_DOCUMENT_QUALITY_PROMPT = r"""

[PROXY DOCUMENT QUALITY AUDIT - REQUIRED]
In addition to the S/V/M classification, audit the document itself. Do not
repair or excuse contradictions. Return this expanded JSON object:
{
  "grade":"TS|S1|S2|S3",
  "secrecy":0,
  "value":0,
  "management":0,
  "confidence":0.0,
  "rationale":"one sentence",
  "document_quality":{
    "structure_appropriate":true,
    "timeline_consistent":true,
    "quantitative_consistent":true,
    "non_repetitive":true
  },
  "quality_issues":[
    {"check":"quantitative_consistent",
     "spans":["exact verbatim span 1","exact verbatim span 2"],
     "reason":"concise explanation of the contradiction"}
  ]
}

Rules for the four checks:
- structure_appropriate: the document has realistic sections/items/table-like
  organization for its type and is not one monolithic block.
- timeline_consistent: all dates, durations, ordering and milestones agree.
- quantitative_consistent: arithmetic, percentages, units, denominators, and
  goal-versus-result statements agree internally. Return false only for a
  concrete conflict or a derived claim that cannot be checked because its
  basis, unit, period, or denominator is missing. Do not reject a correctly
  computed value merely because it looks unusual or deserves further review.
- non_repetitive: false only when a sentence, paragraph, or substantive fact is
  duplicated as filler without a new function. A table value may be restated in
  analysis, a decision, or a follow-up action when the later text interprets it;
  that normal cross-reference is not repetition.

Each value must be the JSON boolean true or false (never a string or null).
For every false value, quality_issues must contain at least one matching issue,
with a non-empty reason and one or more exact, verbatim spans copied from the
document. Use two spans when two claims conflict. Do not report an issue for a
true check. If uncertain, return false and cite the uncertainty-causing span.
For a non_repetitive issue, cite two distinct verbatim spans with different
surrounding text; never duplicate the same span merely to fill the array.
Return JSON only.
"""

PROXY_QUALITY_CHECKS = (
    "structure_appropriate",
    "timeline_consistent",
    "quantitative_consistent",
    "non_repetitive",
)


@dataclass
class LLMLabelResult:
    grade: str
    confidence: float
    factor_scores: dict[str, float]
    rationale: str
    raw_text: str
    parse_ok: bool = True
    parse_error: str | None = None
    # Populated only for the proxy-quality mode. Values intentionally remain
    # uncoerced so the proxy gate can reject strings/nulls rather than fixing
    # malformed model output silently.
    quality_checks: dict[str, object] = field(default_factory=dict)
    quality_issues: object = field(default_factory=list)


class LLMLabeler:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or build_provider()

    def label(
        self,
        text: str,
        *,
        max_tokens: int = 1500,
        temperature: float = 0.1,
        purpose: str = "llm_labeling",
        include_document_quality: bool = False,
    ) -> LLMLabelResult:
        # /no_think: Qwen3 등 thinking 모델의 추론을 꺼 JSON 토큰 잘림(→ S3 default) 방지. 타 provider엔 무해.
        # temperature: 기본 0.1(결정론적 단일 라벨). ConsensusJudge가 self-consistency 표 분산을 위해
        # 0.3~0.7로 올려 호출한다(같은 라벨러를 k회 샘플).
        # purpose: 비용 집계 라벨(second_opinion=서빙 핫패스, llm_labeling=judge/consensus 등).
        prompt = f"/no_think\n분류 대상 문서:\n```\n{text[:4000]}\n```\n\nJSON 응답:"
        system_prompt = (
            SYSTEM_PROMPT + PROXY_DOCUMENT_QUALITY_PROMPT
            if include_document_quality
            else SYSTEM_PROMPT
        )
        resp = self.provider.generate(
            prompt,
            system=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        # [QW] LLM 라벨링 비용 best-effort 기록 — judge·consensus·second_opinion 공통 진입점.
        try:
            from lloydk.services.llm_usage_service import record_llm_usage  # noqa: PLC0415

            record_llm_usage(resp, purpose=purpose)
        except Exception:  # noqa: BLE001
            pass
        parsed = _safe_parse_json(resp.text)
        if not parsed:
            return LLMLabelResult(
                grade="PARSE_FAIL",
                confidence=0.0,
                factor_scores={},
                rationale="",
                raw_text=resp.text,
                parse_ok=False,
                parse_error="llm_json_parse_failed",
            )
        svm_scores = {
            k: float(parsed[k])
            for k in ("secrecy", "value", "management")
            if k in parsed
        }
        raw_quality = parsed.get("document_quality")
        quality_checks = dict(raw_quality) if isinstance(raw_quality, Mapping) else {}
        return LLMLabelResult(
            grade=_reconcile_grade(parsed.get("grade", "S3"), svm_scores),
            # LLM이 보고한 confidence를 [0,1]로 클램프해 사용. 누락·무효(과거엔 스키마에
            # 없어 항상 0.0이 돼 m3 병합 max(llm_conf, score_ratio)에서 LLM 기여가 소거됐다)
            # 시 SVM 요소 점수의 '결정성'에서 폴백 신뢰도를 유도한다.
            confidence=_coerce_confidence(parsed.get("confidence"), svm_scores),
            factor_scores=svm_scores,
            rationale=str(parsed.get("rationale", "")),
            raw_text=resp.text,
            quality_checks=quality_checks,
            quality_issues=parsed.get("quality_issues", []),
        )


def _reconcile_grade(parsed_grade: str, svm: dict[str, float]) -> str:
    """LLM 자가등급을 정본 grade_from_svm(v2.2)과 FNR-safe 정합.

    LLM이 보고한 S/V/M에서 정본 등급을 재도출해, 프롬프트의 등급 산술 드리프트로 인한
    *과소분류*를 구조적으로 막는다(프롬프트 문구와 무관하게 보장):
      - (2,2,1)을 곱4→S1로 깎던 오류 → grade_from_svm=TS 로 교정
      - (2,2,0) 고가치·미관리 비밀을 곱0→S3로 떨구던 누락 → grade_from_svm=S1 로 교정
    두 등급 중 더 위험한(상위, GRADE_RANK 작은) 쪽을 택한다 — LLM 등급을 절대 낮추지 않음
    (FNR-safe). 과대분류는 합의 게이트(rule==llm)가 거르므로 여기선 상향만 한다.
    S/V/M 셋이 다 있지 않거나 등급이 무효면 LLM 자가등급을 그대로 둔다.
    grade_from_svm은 lazy import(rule_engine→seeds 로드를 모듈 임포트 시점에서 분리).
    """
    if parsed_grade not in GRADE_RANK or len(svm) < 3:
        return parsed_grade
    from lloydk.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: PLC0415

    def _lv(x: float) -> int:
        return max(0, min(2, round(float(x))))

    svm_grade = grade_from_svm(
        _lv(svm["secrecy"]), _lv(svm["value"]), _lv(svm["management"])
    )
    return min(parsed_grade, svm_grade, key=lambda g: GRADE_RANK[g])


def _coerce_confidence(raw: object, svm: dict[str, float]) -> float:
    """LLM 보고 confidence를 [0,1]로 정규화. 없거나 무효면 SVM 폴백."""
    try:
        v = float(raw)  # type: ignore[arg-type]
        if 0.0 <= v <= 1.0:
            return round(v, 4)
    except (TypeError, ValueError):
        pass
    return _svm_confidence(svm)


def _svm_confidence(svm: dict[str, float]) -> float:
    """프롬프트 confidence 부재 시 폴백 — 세 요소 판단의 '결정성'.

    0/2점은 결정적(certainty=1.0), 1점은 모호(0.0). 평균을 0.5~1.0로 사상한다
    (완전 모호여도 0.5 — 0.0은 LLM 기여를 다시 소거하므로 피한다). 골든셋 누적 후
    이 폴백이 정답률과 상관되는지 검증해 필요시 margin 기반으로 교체할 것.
    """
    if not svm:
        return 0.5
    cert = [min(1.0, abs(float(v) - 1.0)) for v in svm.values()]
    return round(0.5 + 0.5 * (sum(cert) / len(cert)), 4)


def _safe_parse_json(text: str) -> dict:
    """LLM 응답에서 JSON 블록 추출. noop provider 같은 비-JSON 응답도 graceful."""
    if not text:
        return {}
    text = re.sub(
        r"<think>.*?</think>", "", text, flags=re.S
    )  # Qwen3 thinking 블록 제거
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        out = json.loads(m.group(0))
        if isinstance(out, dict):
            return out
    except json.JSONDecodeError:
        pass
    return {}

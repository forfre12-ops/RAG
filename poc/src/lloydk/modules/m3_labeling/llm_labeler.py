"""LLM 기반 라벨링 — 룰엔진 confidence가 낮거나 경계 사례에서 fallback.

프롬프트 전략: 4대 평가요소 점수화 → 종합 등급 결정.
LLM 응답은 JSON 형식 강제.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from lloydk.adapters.llm import build_provider
from lloydk.adapters.llm.base import LLMProvider

SYSTEM_PROMPT = """당신은 한국 영업비밀 등급분류 가이드(정본)에 정통한 분류 전문가다.
등급 = 비공지성(S) × 경제적 유용성(V) × 비밀관리성(M). 각 요소를 0·1·2점으로 매긴 뒤 곱한다.

[요소 점수 기준]
- 비공지성(S): 0=이미 공개된 정보 / 1=통상적 방법으론 입수 곤란 / 2=보유자를 통하지 않곤 알 수 없음
- 경제적 유용성(V): 0=경제적 가치 없음 / 1=취득·개발에 비용·노력 투입 / 2=상당한 비용·노력 투입
- 비밀관리성(M): 0=공개·관리 안 됨 / 1=임직원 일부만 공유 / 2=승인된 자에 한해 공개

[등급 = S×V×M]
- 8 → TS(특급기밀) · 4 → S1(1급 비밀) · 1 또는 2 → S2(2급 대외비) · 0 → S3(3급 공개)

[중요]
- "외부 공개 시 유출 위험"처럼 *공개를 경고*하는 문장은 그 문서가 **비밀**이라는 뜻(공개 아님). 공개(S=0)는 보도자료·공시·공개특허처럼 *실제로 공표된* 경우만.
- 미탐(고등급을 저등급으로) 금지. 모호하면 한 단계 위.

[예시]
- 중장기 경영계획(S2·V2·M2=8) → TS / 연구개발 보고서·핵심 공정 레시피 → TS~S1
- 인사평가보고서(S1·V2·M2=4) → S1 / 원가구조·자금계획·고객DB → S1
- 조직도(1·1·1=1)·내부 검토안·파트너십 협상안 → S2
- 보도자료·채용공고·공시(S=0) → S3

출력(JSON only, 다른 텍스트 금지):
{"grade":"TS|S1|S2|S3","secrecy":0,"value":0,"management":0,"confidence":0.0,"rationale":"1문장"}
- confidence: 이 등급 판단의 확신도(0~1). 요소 점수가 명확하면 1에 가깝게, 모호하면 낮게."""


@dataclass
class LLMLabelResult:
    grade: str
    confidence: float
    factor_scores: dict[str, float]
    rationale: str
    raw_text: str


class LLMLabeler:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or build_provider()

    def label(self, text: str, *, max_tokens: int = 1500) -> LLMLabelResult:
        # /no_think: Qwen3 등 thinking 모델의 추론을 꺼 JSON 토큰 잘림(→ S3 default) 방지. 타 provider엔 무해.
        prompt = f"/no_think\n분류 대상 문서:\n```\n{text[:4000]}\n```\n\nJSON 응답:"
        resp = self.provider.generate(prompt, system=SYSTEM_PROMPT, max_tokens=max_tokens, temperature=0.1)
        parsed = _safe_parse_json(resp.text)
        svm_scores = {k: float(parsed[k]) for k in ("secrecy", "value", "management") if k in parsed}
        return LLMLabelResult(
            grade=parsed.get("grade", "S3"),
            # LLM이 보고한 confidence를 [0,1]로 클램프해 사용. 누락·무효(과거엔 스키마에
            # 없어 항상 0.0이 돼 m3 병합 max(llm_conf, score_ratio)에서 LLM 기여가 소거됐다)
            # 시 SVM 요소 점수의 '결정성'에서 폴백 신뢰도를 유도한다.
            confidence=_coerce_confidence(parsed.get("confidence"), svm_scores),
            factor_scores=svm_scores,
            rationale=str(parsed.get("rationale", "")),
            raw_text=resp.text,
        )


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
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # Qwen3 thinking 블록 제거
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

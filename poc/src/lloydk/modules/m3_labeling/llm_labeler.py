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
from lloydk.modules.m3_labeling.seeds import GRADE_ORDER

SYSTEM_PROMPT = """당신은 한국 영업비밀 보호 가이드라인에 정통한 문서 분류 전문가다.
입력 문서를 분석하여 보안 등급을 분류하고 4대 평가요소 점수를 산정한다.

등급 체계:
- TS  특급기밀  : 유출 시 회사 존립 위협
- S1  1급 비밀  : 유출 시 중대한 손해
- S2  2급 대외비: 유출 시 경쟁상 불이익
- S3  3급 공개  : 영향 미미 또는 일반 사내자료

4대 평가요소 (각 0~5점):
- ECONOMIC_VALUE     경제적 가치
- NON_PUBLICITY      비공지성
- MANAGEMENT_LEVEL   관리수준
- LEAK_IMPACT        유출 시 영향도

미탐(고등급을 저등급으로 오분류)을 절대 피해라. 모호하면 한 단계 위 등급으로.

출력 형식 (JSON only, 다른 텍스트 금지):
{
  "grade": "TS|S1|S2|S3",
  "confidence": 0.0~1.0,
  "factor_scores": {
    "ECONOMIC_VALUE": 0~5,
    "NON_PUBLICITY": 0~5,
    "MANAGEMENT_LEVEL": 0~5,
    "LEAK_IMPACT": 0~5
  },
  "rationale": "1-2 문장"
}"""


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

    def label(self, text: str, *, max_tokens: int = 512) -> LLMLabelResult:
        prompt = f"분류 대상 문서:\n```\n{text[:4000]}\n```\n\nJSON 응답:"
        resp = self.provider.generate(prompt, system=SYSTEM_PROMPT, max_tokens=max_tokens, temperature=0.1)
        parsed = _safe_parse_json(resp.text)
        return LLMLabelResult(
            grade=parsed.get("grade", "S3"),
            confidence=float(parsed.get("confidence", 0.0)),
            factor_scores={k: float(v) for k, v in (parsed.get("factor_scores") or {}).items()},
            rationale=str(parsed.get("rationale", "")),
            raw_text=resp.text,
        )


def _safe_parse_json(text: str) -> dict:
    """LLM 응답에서 JSON 블록 추출. noop provider 같은 비-JSON 응답도 graceful."""
    if not text:
        return {}
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

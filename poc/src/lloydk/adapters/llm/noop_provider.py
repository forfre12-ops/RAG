"""API 키 없이 결정론적 응답 — 파이프라인 검증·CI용.

설계:
- 프롬프트에서 등급 코드(TS/S1/S2/S3)를 추출
- 해당 등급의 핵심 키워드만 본문에 포함 (다등급 신호어 회피)
- 출력은 generator.py가 파싱하는 JSON 형식
"""

from __future__ import annotations

import re
import time

from lloydk.adapters.llm.base import LLMResponse, UsageRecord, estimate_cost_usd


_GRADE_KW = {
    "TS": "특급기밀, 핵심 원천기술, M&A 계획, 차세대 제품 설계도",
    "S1": "1급 비밀, 공정 노하우, 원가 구조, 고객 데이터베이스, 마케팅 전략",
    "S2": "대외비, 내부 검토, 분기 매출, 사업 계획, 거래처 명단",
    "S3": "보도자료, 공시, 채용 공고, 회사 소개, 이용약관",
}
_GRADE_KR = {"TS": "특급기밀", "S1": "1급 비밀", "S2": "2급 대외비", "S3": "3급 공개"}


def _infer_grade(prompt: str) -> str:
    m = re.search(r"코드:\s*(TS|S1|S2|S3)", prompt)
    if m:
        return m.group(1)
    for code, kr in _GRADE_KR.items():
        if kr in prompt:
            return code
    return "S3"


class NoopProvider:
    """프롬프트의 등급 코드를 추출해 등급별 키워드를 결정론적으로 출력."""

    name = "noop"
    model = "noop"

    def __init__(self) -> None:
        pass

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,  # noqa: ARG002 - API 호환
    ) -> LLMResponse:
        start = time.perf_counter()
        grade = _infer_grade(prompt)
        keywords = _GRADE_KW[grade]
        kw_phrase = ". ".join(f"{kw} 관련 사항" for kw in keywords.split(", "))
        title = f"[{_GRADE_KR[grade]}] 내부 보고서"
        body = f"{kw_phrase}. 본 자료는 {keywords} 관련 내용을 다룬다. {kw_phrase}."

        text = (
            "{\n"
            f'  "title": "{title}",\n'
            f'  "body": "{body}",\n'
            '  "document_type": "내부 보고서",\n'
            '  "dept_hint": "기획팀",\n'
            f'  "rationale_tags": ["{grade}"]\n'
            "}"
        )
        text = text[: max(max_tokens * 4, 512)]

        in_tok = self.count_tokens(prompt + (system or ""))
        out_tok = self.count_tokens(text)
        return LLMResponse(
            text=text,
            usage=UsageRecord(
                provider=self.name,
                model=self.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost_usd=estimate_cost_usd(self.model, in_tok, out_tok),
                latency_ms=int((time.perf_counter() - start) * 1000),
            ),
            meta={"deterministic": True, "grade": grade},
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 3)

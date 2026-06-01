"""API 키 없이 결정론적 응답 — 파이프라인 검증·CI용.

설계 (V2):
- 등급 코드/키워드를 본문/제목에 직접 삽입하지 않음
- 문서 유형과 상황 설명을 기반으로 중립적 더미 내용 생성
- label_match_rate ~25% 예상 (의도적 — 파이프라인 연결 확인용, 품질 측정용 아님)
- 실제 품질 평가는 Qwen3/Claude 등 실 LLM 사용
"""

from __future__ import annotations

import re
import time

from lloydk.adapters.llm.base import LLMResponse, UsageRecord, estimate_cost_usd

# 도메인별 중립 문장 (등급명 없음)
_DOMAIN_BODY = {
    "tech": (
        "본 문서는 기술 연구 및 개발 관련 내부 자료이다. "
        "시스템 설계 검토 결과와 성능 측정 데이터를 포함한다. "
        "관련 팀과의 협의를 통해 다음 단계 일정을 확정할 예정이다."
    ),
    "business": (
        "본 문서는 사업 전략 검토 내용을 정리한 내부 자료이다. "
        "시장 동향 분석 및 파트너십 검토 결과가 포함되어 있다. "
        "경영진 보고를 위한 요약본은 별도 작성 예정이다."
    ),
    "finance": (
        "본 문서는 재무 현황 및 자금 계획을 다룬 내부 자료이다. "
        "분기별 수익 추정치와 비용 집행 현황이 포함되어 있다. "
        "CFO 검토 후 최종 확정될 예정이다."
    ),
    "hr": (
        "본 문서는 인사 운영 관련 내부 검토 자료이다. "
        "조직 구성 방안 및 보상 체계 검토 내용이 포함되어 있다. "
        "인사위원회 심의 후 확정된다."
    ),
    "legal": (
        "본 문서는 계약 및 법무 검토 관련 내부 자료이다. "
        "NDA 초안 검토 결과와 주요 조항 분석 내용이 포함되어 있다. "
        "법무팀 최종 검토 후 서명 절차를 진행한다."
    ),
    "mixed": (
        "본 문서는 내부 검토를 위해 작성된 자료이다. "
        "주요 논의 사항과 의사결정 배경이 포함되어 있다. "
        "관련 부서 확인 후 배포될 예정이다."
    ),
}

_DOMAIN_TITLES = {
    "tech": "기술 검토 보고서",
    "business": "사업 현황 보고서",
    "finance": "재무 현황 보고서",
    "hr": "인사 운영 검토서",
    "legal": "법무 검토 보고서",
    "mixed": "내부 검토 보고서",
}

_DOMAIN_TYPES = {
    "tech": "연구노트",
    "business": "사업계획서",
    "finance": "결산 초안",
    "hr": "임원 평가",
    "legal": "NDA 초안",
    "mixed": "내부 보고서",
}


def _infer_domain(prompt: str) -> str:
    """USER_TEMPLATE_V2의 [도메인] 필드에서 도메인 추출."""
    m = re.search(r"\[도메인\]\s*\n?(.+?)(?:\n|$)", prompt)
    if m:
        raw = m.group(1).strip()
        for key in _DOMAIN_BODY:
            if key in raw:
                return key
    return "mixed"


class NoopProvider:
    """CI/파이프라인 연결 테스트용 결정론적 더미 응답.

    V2: 등급명/키워드 직접 삽입 없음. label_match_rate ~25% 예상 (정상).
    """

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
        temperature: float = 0.7,  # noqa: ARG002
    ) -> LLMResponse:
        start = time.perf_counter()
        domain = _infer_domain(prompt)
        title = _DOMAIN_TITLES.get(domain, "내부 보고서")
        body = _DOMAIN_BODY.get(domain, _DOMAIN_BODY["mixed"])
        doc_type = _DOMAIN_TYPES.get(domain, "내부 보고서")

        # JSON 이스케이프 (개행 없는 body라 간단)
        text = (
            "{\n"
            f'  "title": "{title}",\n'
            f'  "body": "{body}",\n'
            f'  "document_type": "{doc_type}",\n'
            '  "dept_hint": "기획팀",\n'
            '  "rationale_tags": ["noop"]\n'
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
            meta={"deterministic": True, "domain": domain},
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 3)

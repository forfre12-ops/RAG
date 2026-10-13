"""API 키 없이 결정론적 응답 — 파이프라인 검증·CI용.

설계 (V2):
- 등급 코드/키워드를 본문/제목에 직접 삽입하지 않음
- 문서 유형과 상황 설명을 기반으로 중립적 더미 내용 생성
- label_match_rate ~25% 예상 (의도적 — 파이프라인 연결 확인용, 품질 측정용 아님)
- 실제 품질 평가는 Qwen3/Claude 등 실 LLM 사용
"""

from __future__ import annotations

import json
import re
import time

from koipa.adapters.llm.base import LLMResponse, UsageRecord, estimate_cost_usd

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


def build_noop_fixture_body(domain: str) -> str:
    """파이프라인 검증용으로도 읽을 수 있는, 등급 중립의 결정론적 문서 본문.

    ``noop``은 실제 생성기가 아니므로 결과가 학습에 들어가면 안 된다. 다만 한 문장짜리
    응답은 콘솔의 ``문서 열기``에서 빈 화면처럼 보여, 검수 흐름 자체를 확인할 수 없었다.
    이 본문은 테스트 문서임을 밝히되 문서형 단락·검토 항목을 갖춘다. 등급·공개범위·피해
    같은 판정 단서는 어떤 도메인에도 넣지 않는다.
    """
    key = domain if domain in _DOMAIN_BODY else "mixed"
    summary = _DOMAIN_BODY[key]
    doc_type = _DOMAIN_TYPES.get(key, _DOMAIN_TYPES["mixed"])
    return (
        "1. 작성 목적\n"
        f"본 문서는 {doc_type} 형식의 파이프라인 검증용 테스트 자료이다. "
        "실제 업무 의사결정이나 배포 자료로 사용하지 않는다.\n\n"
        "2. 검토 배경\n"
        f"{summary}\n\n"
        "3. 확인 항목\n"
        "가. 문서 제목·유형·생성 제공자 정보가 화면에 일치하게 표시되는지 확인한다.\n"
        "나. 본문 열기, 검토 상태 변경, 이력 조회가 같은 문서를 가리키는지 확인한다.\n"
        "다. 테스트 문서는 학습 데이터에 포함되지 않는지 확인한다.\n\n"
        "4. 후속 조치\n"
        "운영자는 실제 생성 제공자로 만든 문서를 별도 검토한 뒤, 필요한 경우에만 다음 절차를 진행한다."
    )


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
        json_schema: dict | None = None,
    ) -> LLMResponse:
        """json_schema 는 받되 쓰지 않는다 — 이 provider 의 출력은 이미 고정 JSON 이다.

        인자를 받는 이유는 호출부의 능력 탐지(generate 시그니처에 json_schema 가 있는가)
        때문이다. 받지 않으면 CI 가 구조화 출력 경로를 한 번도 밟지 않는다.
        """
        start = time.perf_counter()
        domain = _infer_domain(prompt)
        title = _DOMAIN_TITLES.get(domain, "내부 보고서")
        body = build_noop_fixture_body(domain)
        doc_type = _DOMAIN_TYPES.get(domain, "내부 보고서")

        # 본문에 문단 개행이 있으므로 손으로 JSON 문자열을 만들지 않는다. 수동 조립하면
        # 개행이 깨져 파서가 raw body 경로로 떨어지고, 검수 화면에는 다시 짧은 문장만 남는다.
        text = json.dumps(
            {
                "title": title,
                "body": body,
                "document_type": doc_type,
                "dept_hint": "기획팀",
                "rationale_tags": ["noop"],
            },
            ensure_ascii=False,
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
            meta={
                "deterministic": True,
                "domain": domain,
                "json_schema": bool(json_schema),
            },
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 3)

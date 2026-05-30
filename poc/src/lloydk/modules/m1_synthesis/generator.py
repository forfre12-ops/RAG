"""M1 SyntheticDocGenerator — LLM Provider 어댑터를 통해 합성 문서 생성.

핵심 변경: build_provider() 사용으로 noop/anthropic/openai/vllm 교체 가능.
Noop provider를 쓰면 API 키 없이 결정론적 더미 문서로 파이프라인 검증 가능.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from lloydk.adapters.llm import build_provider
from lloydk.adapters.llm.base import LLMProvider, UsageRecord

# Grade enum은 SynthRequest.target_grade에서 .value 처리만 하므로 별도 import 불필요.


GRADE_KR = {
    "TS": "특급기밀",
    "S1": "1급 비밀",
    "S2": "2급 대외비",
    "S3": "3급 공개",
}

GRADE_KEYWORDS = {
    "TS": "특급기밀, 핵심 원천기술, M&A 계획, 차세대 제품 설계도, 임직원 인사 이동",
    "S1": "1급 비밀, 영업비밀, 공정 노하우, 원가 구조, 고객 데이터베이스, 마케팅 전략",
    "S2": "대외비, 내부 검토, 분기 매출, 사업 계획, 거래처 명단",
    "S3": "보도자료, 공시, 채용 공고, 회사 소개, 이용약관, 외부 공지",
}

SYSTEM_PROMPT = """당신은 한국의 영업비밀 등급분류 가이드 전문가다.
주어진 등급 정의를 근거로 가상의 사내 문서를 작성한다.

[준수]
- 실재 기업명/인명/주민번호/연락처/이메일 등 PII 금지
- 가상 표기: [가상기업A], [홍길동(가명)]
- 출력은 반드시 다음 JSON 한 객체:
  {"title": str, "body": str, "document_type": str, "dept_hint": str, "rationale_tags": [str, ...]}
- 그 외 텍스트(설명, 코드펜스) 출력 금지
- 본문에 핵심 키워드가 자연스럽게 등장하도록 작성"""

USER_TEMPLATE = """[등급]
코드: {grade_code} ({grade_name})

[등급 키워드 (자연스럽게 본문에 녹여 사용)]
{keywords}

[도메인]
{domain}

[문서 유형 후보]
{doc_types}

[작성 길이]
한국어 {len_min}~{len_max}자

위 조건에 부합하는 가상의 사내 문서를 JSON 객체로 작성하시오."""

DOMAIN_DOC_TYPES = {
    "tech": "연구노트, 설계명세, 시험성적서, 알고리즘 설명서",
    "business": "사업계획서, 시장분석, 투자제안서, 파트너십 검토",
    "finance": "결산 초안, 자금조달 계획, 손익 추정",
    "hr": "임원 평가, 보상 체계, 인사 이동안",
    "legal": "NDA 초안, MOU, 라이선스 계약 검토",
    "mixed": "내부 보고서, 회의록, 의사결정 문서",
}

_PII_PATTERNS = [
    re.compile(r"\d{6}-\d{7}"),
    re.compile(r"\d{3}-\d{3,4}-\d{4}"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
]


@dataclass
class SynthRequest:
    target_grade: str       # TS|S1|S2|S3
    domain: str = "mixed"
    count: int = 1
    len_min: int = 600
    len_max: int = 2000


@dataclass
class SynthDoc:
    target_grade: str
    domain: str
    title: str
    body: str
    document_type: str
    dept_hint: str
    rationale_tags: list[str]
    llm_provider: str
    usage: Optional[UsageRecord] = None
    pii_violations: list[str] = field(default_factory=list)
    parse_error: Optional[str] = None


class SyntheticDocGenerator:
    def __init__(self, llm: Optional[LLMProvider] = None) -> None:
        self.llm = llm or build_provider()

    def _pii_violations(self, text: str) -> list[str]:
        return [p.pattern for p in _PII_PATTERNS if p.search(text)]

    def _parse(self, raw: str) -> dict | None:
        if not raw:
            return None
        s = raw.strip()
        if s.startswith("```"):
            s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.DOTALL)
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", s, re.S)
            if not m:
                return None
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None

    def generate_one(self, req: SynthRequest) -> SynthDoc:
        grade_code = req.target_grade.value if hasattr(req.target_grade, "value") else str(req.target_grade)
        user = USER_TEMPLATE.format(
            grade_code=grade_code,
            grade_name=GRADE_KR.get(grade_code, grade_code),
            keywords=GRADE_KEYWORDS.get(grade_code, ""),
            domain=req.domain,
            doc_types=DOMAIN_DOC_TYPES.get(req.domain, DOMAIN_DOC_TYPES["mixed"]),
            len_min=req.len_min,
            len_max=req.len_max,
        )
        # C3-7 (2026-05-30): JSON 파싱 실패 시 최대 2회 재시도. Solar 등 JSON 출력
        # 안정성 약한 LLM 에서 76.5% → 30% 미만으로 실패율 감소 기대.
        # 재시도 시 temperature 낮춰 deterministic 시도 + system prompt 강화.
        max_retries = 2
        attempt = 0
        resp = self.llm.generate(user, system=SYSTEM_PROMPT, temperature=0.7, max_tokens=2048)
        parsed = self._parse(resp.text)
        while parsed is None and attempt < max_retries:
            attempt += 1
            # 재시도: temperature 0.3, system prompt 에 "반드시 유효한 JSON 만 출력" 추가
            retry_system = SYSTEM_PROMPT + "\n\n[중요] 반드시 유효한 JSON 객체 1개만 출력하세요. 코드블록·설명·주석 모두 금지."
            resp = self.llm.generate(user, system=retry_system, temperature=0.3, max_tokens=2048)
            parsed = self._parse(resp.text)

        if parsed is None:
            # noop provider 등은 JSON이 아님 — fallback으로 텍스트 그대로 body 사용
            body = resp.text or _fallback_body(grade_code, req.domain)
            title = f"[{GRADE_KR.get(grade_code)}] {DOMAIN_DOC_TYPES.get(req.domain, '내부 자료')} 합성 v{abs(hash(user)) % 10000:04d}"
            doc_type = DOMAIN_DOC_TYPES.get(req.domain, "내부 자료").split(",")[0].strip()
            return SynthDoc(
                target_grade=grade_code,
                domain=req.domain,
                title=title,
                body=body,
                document_type=doc_type,
                dept_hint="",
                rationale_tags=[grade_code],
                llm_provider=self.llm.name,
                usage=resp.usage,
                pii_violations=self._pii_violations(body),
                parse_error="non-json response",
            )

        body = parsed.get("body", "") or ""
        return SynthDoc(
            target_grade=grade_code,
            domain=req.domain,
            title=parsed.get("title", "") or "",
            body=body,
            document_type=parsed.get("document_type", "") or "",
            dept_hint=parsed.get("dept_hint", "") or "",
            rationale_tags=list(parsed.get("rationale_tags", []) or []),
            llm_provider=self.llm.name,
            usage=resp.usage,
            pii_violations=self._pii_violations(body),
        )

    def generate(self, req: SynthRequest) -> list[SynthDoc]:
        return [self.generate_one(req) for _ in range(req.count)]


def _fallback_body(grade_code: str, domain: str) -> str:
    """Noop provider 등 비-LLM 모드용 fallback. 등급별 키워드를 본문에 명시 포함."""
    kws = GRADE_KEYWORDS.get(grade_code, "")
    # 키워드를 2회씩 본문에 포함하여 룰 라벨러가 확실히 매칭하도록 보강
    kw_sentences = ". ".join(f"{kw} 관련 사항을 다룬다" for kw in kws.split(", ")) + "."
    return (
        f"본 문서는 {GRADE_KR.get(grade_code, grade_code)} 등급 {domain} 도메인 자료이다.\n\n"
        "1. 개요\n"
        f"{kw_sentences}\n\n"
        "2. 주요 내용\n"
        f"본 자료는 {kws} 등 핵심 키워드를 포함한다. "
        "합성 파이프라인 검증을 위해 결정론적으로 생성되었으며, 실제 LLM 사용 시에는 "
        "가이드라인 기반의 자연스러운 사내 문서로 채워진다.\n\n"
        "3. 결론\n"
        f"{kws}에 관한 검토 결과를 종합하여 차기 회의에 보고한다."
    )

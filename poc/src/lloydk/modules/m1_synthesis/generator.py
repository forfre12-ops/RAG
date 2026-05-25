from __future__ import annotations
import json
import re
from dataclasses import dataclass
from typing import Optional

from lloydk.schemas.common import Grade
from lloydk.adapters.llm import get_llm, LLMAdapter


GRADE_KR = {
    Grade.TS: "특급기밀",
    Grade.S1: "1급 비밀",
    Grade.S2: "2급 대외비",
    Grade.S3: "3급 공개",
}

SYSTEM_PROMPT = """\
당신은 한국의 영업비밀 등급분류 가이드 전문가다.
주어진 가이드 컨텍스트와 등급 정의를 근거로 가상의 사내 문서를 작성한다.

[준수]
- 실재 기업명/인명/주민번호/연락처/이메일 등 PII 금지
- 가상 표기 권장: [가상기업A], [홍길동], 등
- 출력은 반드시 다음 JSON 한 객체:
  {"title": str, "body": str, "document_type": str,
   "dept_hint": str, "rationale_tags": [str, ...]}
- 그 외 텍스트(설명, 코드펜스) 출력 금지
"""

USER_TEMPLATE = """\
[가이드 컨텍스트]
{guide_context}

[목표 등급]
코드: {grade_code}
명칭: {grade_name}

[도메인]
{domain}

[문서 유형 후보]
{doc_types}

[작성 길이]
한국어 {len_min}~{len_max}자

위 조건에 부합하는 가상의 사내 문서를 JSON 객체로 작성하시오.
"""

DOMAIN_DOC_TYPES = {
    "tech": "연구노트, 설계명세, 시험성적서, 알고리즘 설명서",
    "business": "사업계획서, 시장분석, 투자제안서, 파트너십 검토",
    "finance": "결산 초안, 자금조달 계획, 손익 추정",
    "hr": "임원 평가, 보상 체계, 인사 이동안",
    "legal": "NDA 초안, MOU, 라이선스 계약 검토",
    "mixed": "내부 보고서, 회의록, 의사결정 문서",
}

_PII_PATTERNS = [
    re.compile(r"\d{6}-\d{7}"),                 # 주민번호 패턴
    re.compile(r"\d{3}-\d{3,4}-\d{4}"),         # 전화
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),    # 이메일
]


@dataclass
class SynthRequest:
    target_grade: Grade
    domain: str = "mixed"
    count: int = 1
    guide_chunks: list[str] | None = None
    len_min: int = 1000
    len_max: int = 3000


@dataclass
class SynthDoc:
    target_grade: Grade
    domain: str
    title: str
    body: str
    document_type: str
    dept_hint: str
    rationale_tags: list[str]
    llm_provider: str


class SyntheticDocGenerator:
    def __init__(self, llm: Optional[LLMAdapter] = None):
        self.llm = llm or get_llm()

    def _pii_violations(self, text: str) -> list[str]:
        out = []
        for pat in _PII_PATTERNS:
            if pat.search(text):
                out.append(pat.pattern)
        return out

    def _parse(self, raw: str) -> dict:
        s = raw.strip()
        if s.startswith("```"):
            s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.DOTALL)
        return json.loads(s)

    def generate_one(self, req: SynthRequest) -> SynthDoc:
        guide_ctx = "\n\n".join(req.guide_chunks or ["(가이드 미제공)"])
        user = USER_TEMPLATE.format(
            guide_context=guide_ctx[:8000],
            grade_code=req.target_grade.value,
            grade_name=GRADE_KR[req.target_grade],
            domain=req.domain,
            doc_types=DOMAIN_DOC_TYPES.get(req.domain, DOMAIN_DOC_TYPES["mixed"]),
            len_min=req.len_min,
            len_max=req.len_max,
        )
        raw = self.llm.complete(SYSTEM_PROMPT, user, temperature=0.7, max_tokens=4000)
        data = self._parse(raw)
        body = data["body"]
        if self._pii_violations(body):
            raise ValueError("PII detected in generated body")
        return SynthDoc(
            target_grade=req.target_grade,
            domain=req.domain,
            title=data["title"],
            body=body,
            document_type=data["document_type"],
            dept_hint=data.get("dept_hint", ""),
            rationale_tags=data.get("rationale_tags", []),
            llm_provider=self.llm.name,
        )

    def generate(self, req: SynthRequest) -> list[SynthDoc]:
        return [self.generate_one(req) for _ in range(req.count)]

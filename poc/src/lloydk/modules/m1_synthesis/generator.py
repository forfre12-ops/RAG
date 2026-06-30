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

# GRADE_KEYWORDS는 룰 라벨러(M3)에서 weak label 생성용으로만 사용.
# generator 프롬프트에 직접 주입하지 않는다 — 키워드 leakage 차단.
GRADE_KEYWORDS = {
    "TS": (
        "특급기밀, 핵심 원천기술, M&A 계획, 차세대 제품 설계도, 임직원 인사 이동, "
        "암호화 알고리즘 키, 제로데이 취약점, HSM 마스터 시드, 루트 CA 개인키, "
        "반도체 공정 레시피, EUV 공정 파라미터, 신약 후보물질, 임상 1상 결과, "
        "인수합병 실사 보고서, 비공개 합병 가격, 비공개 IPO 일정, "
        "유도무기 제어 알고리즘, 국가핵심기술, 방위산업기술, "
        "배터리 양극재 조성, 전고체 전해질 조성, 자율주행 핵심 알고리즘, "
        "RLHF 보상 모델 가중치, 기초모델 사전학습 데이터셋"
    ),
    "S1": "1급 비밀, 영업비밀, 공정 노하우, 원가 구조, 고객 데이터베이스, 마케팅 전략",
    "S2": "대외비, 내부 검토, 분기 매출, 사업 계획, 거래처 명단",
    "S3": "보도자료, 공시, 채용 공고, 회사 소개, 이용약관, 외부 공지",
}

# V2: 등급명 대신 상황/맥락으로 문서 특성을 유도.
# 생성된 문서가 등급을 "직접 표기"하는 것이 아니라 "판단 근거"를 담도록 유도.
GRADE_SITUATION_PROMPTS = {
    "TS": {
        "situation": (
            "유출 시 회사 존립 또는 국가 안보에 심각한 피해를 줄 수 있는 최고 전략 자료. "
            "예: 아직 공개되지 않은 핵심 원천기술 내용, 진행 중인 비공개 M&A 실사, "
            "암호 키·취약점 등 보안 운영 정보, 정부 방산 기밀에 준하는 기술 명세."
        ),
        "disclosure_scope": "극소수 C-level 임원 및 특정 업무 담당자만 접근 가능. 외부 공유 절대 불가.",
        "harm_potential": "경쟁사 기술 복제, 진행 중인 인수협상 무력화, 보안 인프라 침해 가능.",
    },
    "S1": {
        "situation": (
            "유출 시 회사의 경쟁우위나 사업 기회에 상당한 피해를 주는 영업비밀 수준 자료. "
            "예: 핵심 공정 노하우, 원가 구조, 주요 고객사 데이터, 미공개 마케팅 전략."
        ),
        "disclosure_scope": "해당 사업부 임원·팀장급 이상. 외부 공유 금지.",
        "harm_potential": "경쟁사 가격 역산, 고객 이탈 유도, 영업 전략 무력화 가능.",
    },
    "S2": {
        "situation": (
            "외부 공개 시 회사 이미지나 협상력에 불이익을 줄 수 있는 내부 검토 자료. "
            "예: 확정 전 분기 매출 초안, 내부 사업 계획 검토안, 거래처 협상 조건."
        ),
        "disclosure_scope": "내부 검토 단계. 부서 내 공유 가능, 외부 미공개.",
        "harm_potential": "협상력 약화, 미확정 정보로 인한 시장 혼란 가능.",
    },
    "S3": {
        "situation": (
            "이미 외부에 공개되었거나 공개를 전제로 작성된 자료로, 누구나 열람해도 회사에 "
            "불이익이 없다. 예: 배포된 보도자료, 채용 공고, 법정 공시, 회사 소개, 이용약관, "
            "고객 안내 FAQ. **미공개 수치·내부 전략·협상 조건·원가·매출 추정·고객 명단·"
            "기술 사양은 절대 포함하지 않는다** — 포함되면 더 이상 공개 자료가 아니다. "
            "문체도 대외 홍보·안내문처럼 작성한다."
        ),
        "disclosure_scope": "일반 대중에게 공개됨(또는 공개 예정). 접근 제한 없음.",
        "harm_potential": "없음 — 이미 공개된 정보 수준이라 유출로 인한 피해가 성립하지 않는다.",
    },
}

SYSTEM_PROMPT = """당신은 한국의 영업비밀 등급분류 가이드 전문가다.
주어진 문서 상황·맥락을 읽고 해당 상황에 실재할 법한 가상의 사내 문서를 작성한다.

[준수]
- 실재 기업명/인명/주민번호/연락처/이메일 등 PII 금지
- 가상 표기: [가상기업A], [홍길동(가명)]
- 출력은 반드시 다음 JSON 한 객체:
  {"title": str, "body": str, "document_type": str, "dept_hint": str, "rationale_tags": [str, ...]}
- 그 외 텍스트(설명, 코드펜스) 출력 금지

[중요 — 등급 표기 금지]
- 제목·본문에 등급명(TS, S1, S2, S3, 특급기밀, 1급 비밀, 2급, 3급, 대외비, 기밀, 비밀, 극비) 직접 기재 금지
- "이 문서는 ○급 비밀입니다" 같은 분류 표기 금지
- 대신 문서의 내용과 맥락 자체가 민감도를 드러내도록 작성"""

USER_TEMPLATE_V2 = """[문서 상황]
{situation}

[공개 범위]
{disclosure_scope}

[잠재적 피해 가능성]
{harm_potential}

[도메인]
{domain}

[문서 유형 후보]
{doc_types}

[작성 길이]
body는 한국어 {len_min}자 이상 {len_max}자 이내. 단답 금지 — 여러 단락으로, 구체적 항목·수치·일정·금액·기술 사양 등 세부를 실제 사내 문서처럼 충실히 작성.

위 상황에 해당하는 가상의 사내 문서를 JSON 객체로 작성하시오.
- body는 반드시 {len_min}자 이상의 상세 문서(여러 단락).
- 등급명(비밀, 기밀, TS, S1, S2, S3, 대외비, 극비 등)은 문서 내용에 포함하지 마시오."""

DOMAIN_DOC_TYPES = {
    "tech": "연구노트, 설계명세, 시험성적서, 알고리즘 설명서",
    "business": "사업계획서, 시장분석, 투자제안서, 파트너십 검토",
    "finance": "결산 초안, 자금조달 계획, 손익 추정",
    "hr": "임원 평가, 보상 체계, 인사 이동안",
    "legal": "NDA 초안, MOU, 라이선스 계약 검토",
    "mixed": "내부 보고서, 회의록, 의사결정 문서",
    # 공개 전용 도메인 (S3 — 이미 공개된 대외 자료. 내부 수치·전략 미포함)
    "public": "보도자료, 채용 공고, 법정 공시 자료, 회사 소개 페이지, 이용약관, 고객 안내 FAQ, 외부 블로그 글",
    # TS 전용 특화 도메인 (FNR 개선 목적)
    "security": "암호 키 관리 보고서, 취약점 분석서, HSM 운영 지침, 보안 인증서 관리 대장",
    "ma": "인수합병 실사 보고서, 기업가치 평가서, 주식 매수 계획안, 비공개 합병 의향서",
    "defense": "방위산업 기술 명세, 국가핵심기술 보호 계획, 무기체계 설계 검토, 방산물자 기술 문서",
    "semiconductor": "공정 레시피 명세, EUV 파라미터 설계서, 수율 개선 연구노트, 반도체 설계 도면",
    "bio": "신약 후보물질 연구노트, 임상시험 프로토콜, 화합물 합성 경로, FDA 전략 기획서",
    "ai": "사전학습 데이터셋 명세, 모델 가중치 관리 문서, RLHF 보상 설계서, 핵심 알고리즘 특허 전략",
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

    @staticmethod
    def _record_usage(resp: object) -> None:
        """[QW] 합성 LLM 호출 비용 best-effort 기록 (purpose='synthesis')."""
        try:
            from lloydk.services.llm_usage_service import record_llm_usage  # noqa: PLC0415
            record_llm_usage(resp, purpose="synthesis")
        except Exception:  # noqa: BLE001
            pass

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
        situation = GRADE_SITUATION_PROMPTS.get(grade_code, GRADE_SITUATION_PROMPTS["S3"])
        user = USER_TEMPLATE_V2.format(
            situation=situation["situation"],
            disclosure_scope=situation["disclosure_scope"],
            harm_potential=situation["harm_potential"],
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
        resp = self.llm.generate(user, system=SYSTEM_PROMPT, temperature=0.7, max_tokens=3500)
        self._record_usage(resp)
        parsed = self._parse(resp.text)
        while parsed is None and attempt < max_retries:
            attempt += 1
            # 재시도: temperature 0.3, system prompt 에 "반드시 유효한 JSON 만 출력" 추가
            retry_system = SYSTEM_PROMPT + "\n\n[중요] 반드시 유효한 JSON 객체 1개만 출력하세요. 코드블록·설명·주석 모두 금지."
            resp = self.llm.generate(user, system=retry_system, temperature=0.3, max_tokens=3500)
            self._record_usage(resp)  # 재시도도 실제 LLM 비용 — 누락 없이 기록
            parsed = self._parse(resp.text)

        if parsed is None:
            # noop provider 등은 JSON이 아님 — fallback으로 텍스트 그대로 body 사용
            body = resp.text or _fallback_body(grade_code, req.domain)
            title = f"{DOMAIN_DOC_TYPES.get(req.domain, '내부 자료').split(',')[0].strip()} 합성 v{abs(hash(user)) % 10000:04d}"
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
    """Noop provider / CI 파이프라인 연결 테스트용 fallback.

    실제 품질 평가에 사용하지 않는다 (label_source: noop_fallback 으로 구분).
    등급명·키워드를 본문에 직접 삽입하지 않는다.
    """
    situation = GRADE_SITUATION_PROMPTS.get(grade_code, GRADE_SITUATION_PROMPTS["S3"])
    return (
        f"[Noop fallback — {domain} 도메인 합성 문서]\n\n"
        "1. 문서 개요\n"
        f"본 자료는 {situation['disclosure_scope']} 조건에 해당하는 내부 문서이다.\n\n"
        "2. 주요 내용\n"
        f"{situation['situation']}\n\n"
        "3. 결론\n"
        "합성 파이프라인 연결 테스트 목적으로 생성되었으며, "
        "실제 LLM 호출 시 이 내용은 해당 맥락에 맞는 자연스러운 문서로 대체된다."
    )

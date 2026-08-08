"""M1 SyntheticDocGenerator — LLM Provider 어댑터를 통해 합성 문서 생성.

핵심 변경: build_provider() 사용으로 noop/anthropic/openai/vllm 교체 가능.
Noop provider를 쓰면 API 키 없이 결정론적 더미 문서로 파이프라인 검증 가능.
"""

from __future__ import annotations

import hashlib
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

SYSTEM_PROMPT = """당신은 한국 조직에서 쓰이는 현실적인 사내 문서를 작성하는 전문 문서 작성자다.
주어진 문서 상황·맥락을 읽고 해당 상황에 실재할 법한 가상의 사내 문서를 작성한다.

[준수]
- 실재 기업명/인명/주민번호/연락처/이메일 등 PII 금지
- 사람 이름은 가명도 만들지 말고 [공정책임자A], 품질관리팀처럼 역할 식별자만 사용
- 사용자 요청에 정량 사실 출력 금지 또는 숫자 금지가 있으면 그 요청이 아래의 수치·날짜·표 작성
  지시보다 항상 우선한다. 이 경우 제목·본문·표에 아라비아 숫자·날짜·금액·단위·범위·비율을 쓰지 않고,
  정성적인 관찰·책임·판단·조치만 작성한다.
- 수치·날짜·단위·목표와 실적 사이에 산술 또는 시간 모순이 없도록 자체 점검
- 한 문서의 절대 날짜는 하나의 기준연도 안에서 시간순으로 배치하고, 기준연도가 주어지지
  않았으면 1주차·시험 종료 후 10영업일처럼 상대 시점을 사용한다. 후속조치 기한은 원인분석·
  시험·회의가 끝난 뒤여야 한다.
- 표에 적은 정상범위·경고값·실패경계와 본문 원인 설명을 일치시키고, 경계 미만 값이 실패
  원인이라면 다른 조건과의 결합효과 또는 예외 근거를 명시한다.
- 차이·증감률·합계·평균·절감액 같은 파생값은 기초값으로 다시 계산하고 분모·기간·단위를 명시한다.
  의사결정에 필요하지 않거나 검산이 확실하지 않은 파생값은 추정해서 채우지 말고 생략한다.
- 변경 전→후 차이는 두 기초값을 직접 빼서 검산한다. 예를 들어 150에서 160으로
  바뀌면 차이는 10이며, 임계치나 다른 기준값을 실제 차이처럼 서술하지 않는다.
- 표의 모든 헤더·데이터 셀을 구체적인 값이나 "해당 없음"과 그 사유로 채운다. 빈 셀,
  하이픈(-), 긴 대시(—)를 미작성 값 대신 사용하지 않는다.
- 표로 문서를 끝내지 않는다. 마지막 표 뒤에 결론과 후속 조치 문단을 쓰고 책임 역할,
  기한, 완료 판정 기준을 다시 확인한 뒤 JSON을 닫는다.
- 문서 유형에 맞는 절 제목·항목·표 형식·승인/조치란을 실제 본문 구조로 표현하고 장문 서술 하나로 뭉치지 않기
- 출력은 반드시 다음 JSON 한 객체:
  {"title": str, "body": str, "document_type": str, "dept_hint": str, "rationale_tags": [str, ...]}
- 요청한 body 상한에 가까워지면 새 절이나 부록을 시작하지 말고 현재 문장을 마친 뒤
  JSON 문자열과 객체를 정상적으로 닫는다. 유효한 JSON 완결을 추가 내용보다 우선한다.
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

[구조·완성도 요구]
{structure_requirements}

[사실 원장 우선순위]
구조·완성도 요구에 코드가 정한 사실·수치 제한 또는 정량 사실 출력 금지가 있으면, 그 제한이
아래의 일반적인 구체성 요구보다 우선한다. 원장 밖의 기초 수치나 파생 수치를 임의로 보충하지
않으며, 정량 출력 금지인 경우 제목·본문·표에 아라비아 숫자를 쓰지 않는다.

[작성 길이]
body는 한국어 {len_min}자 이상 {len_max}자 이내. 단답 금지 — 여러 단락으로, 관찰 과정, 책임 역할,
의사결정 근거, 예외 처리, 후속 조치 등 서로 다른 세부를 실제 사내 문서처럼 충실히 작성.
body가 {len_max}자에 가까워지면 새 내용 추가를 멈추고 JSON의 닫는 따옴표와 중괄호까지 반드시 출력한다. JSON 완결을 분량 확대보다 우선한다.

{revision_context}

위 상황에 해당하는 가상의 사내 문서를 JSON 객체로 작성하시오.
- body는 반드시 {len_min}자 이상의 상세 문서(여러 단락).
- 문서 계열 조건에 지정된 구성 순서가 본문에서 섹션 제목이나 항목으로 확인되어야 한다.
- 같은 사실을 표현만 바꾸어 반복하지 말고, 관찰·판단·조치의 연결을 일관되게 유지한다.
- 정량 출력이 허용된 경우에만 표와 본문의 기초값을 일치시키고 차이·비율·합계를 다시 계산한다. 검산할 수 없는 파생 수치는 쓰지 않는다.
- 정량 출력이 허용된 경우에만 정상범위·실패경계와 원인 설명, 시험·결과·후속조치의 시간 순서를 대조한다.
- 정량 출력이 허용된 경우에만 변경 전·후 차이를 직접 검산한다. 표에는 빈 셀이나 대시 placeholder를 두지 않는다.
- 마지막 표 뒤에는 반드시 결론과 책임 역할·기한·완료 기준이 있는 후속 조치 문단을 둔다.
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


class PromptLanguageContractError(ValueError):
    """A required Korean prompt was damaged or decoded with the wrong codec."""


def _hangul_syllable_count(value: str) -> int:
    return sum(0xAC00 <= ord(char) <= 0xD7A3 for char in value)


def _require_korean_prompt_text(
    value: object,
    *,
    field: str,
    min_hangul: int = 3,
    min_hangul_ratio: float = 0.55,
) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PromptLanguageContractError(f"Korean prompt text is missing: {field}")
    if "\ufffd" in value or any(
        ord(char) < 32 and char not in "\n\r\t" for char in value
    ):
        raise PromptLanguageContractError(
            f"Korean prompt text has invalid Unicode: {field}"
        )
    hangul = _hangul_syllable_count(value)
    alphabetic = sum(char.isalpha() for char in value)
    if hangul < min_hangul or (alphabetic and hangul / alphabetic < min_hangul_ratio):
        raise PromptLanguageContractError(
            f"Korean prompt failed the UTF-8/Hangul integrity gate: {field}"
        )


def validate_generator_prompt_contract() -> None:
    """Validate every static Korean input before constructing a generator."""
    _require_korean_prompt_text(SYSTEM_PROMPT, field="SYSTEM_PROMPT", min_hangul=100)
    _require_korean_prompt_text(
        USER_TEMPLATE_V2, field="USER_TEMPLATE_V2", min_hangul=100
    )
    for grade, fields in GRADE_SITUATION_PROMPTS.items():
        for prompt_field in ("situation", "disclosure_scope", "harm_potential"):
            _require_korean_prompt_text(
                fields.get(prompt_field),
                field=f"GRADE_SITUATION_PROMPTS[{grade!r}].{prompt_field}",
            )
    for domain, value in DOMAIN_DOC_TYPES.items():
        _require_korean_prompt_text(value, field=f"DOMAIN_DOC_TYPES[{domain!r}]")


_PII_PATTERNS = [
    re.compile(r"\d{6}-\d{7}"),
    re.compile(r"\d{3}-\d{3,4}-\d{4}"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
]


@dataclass
class SynthRequest:
    target_grade: str  # TS|S1|S2|S3
    domain: str = "mixed"
    count: int = 1
    len_min: int = 600
    len_max: int = 2000
    # Optional proxy-corpus controls.  Existing generic generation remains
    # unchanged when these are empty; catalog-driven generation can require a
    # document-shaped scenario instead of a short grade-shaped prompt.
    scenario_context: str = ""
    disclosure_scope: str = ""
    harm_potential: str = ""
    document_type_hint: str = ""
    # Korean prose can consume more than one token per character.  Catalog
    # runners may raise this for long document profiles while keeping the
    # legacy default unchanged.
    max_output_tokens: int = 3500
    # Optional, catalog-specific controls.  They are deliberately empty by
    # default so legacy/generic callers keep their previous request contract.
    structure_requirements: str = ""
    revision_context: str = ""


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
    llm_model: str = ""
    usage: Optional[UsageRecord] = None
    pii_violations: list[str] = field(default_factory=list)
    parse_error: Optional[str] = None
    # [C16] 본문 출처 식별 — None=정상 생성(JSON 파싱 OK). "noop_fallback"=resp.text가 비어
    # placeholder 본문 사용(CI 연결 테스트, 학습 편입 금지 마커). "llm_nonjson"=실 LLM이 비-JSON
    # 텍스트를 줘서 raw를 body로 사용. parse_error만으론 뒤 둘이 뭉뚱그려져 grep 식별 불가였다.
    label_source: Optional[str] = None
    # 원문을 중복 보관하지 않고도 출력 절단/빈 응답/비정상 JSON을 감사할 수 있는
    # 호출별 메타데이터. 내부 JSON 재시도에서 먼저 실패하고 성공한 경우도 보존한다.
    response_audit: list[dict[str, object]] = field(default_factory=list)


class SyntheticDocGenerator:
    def __init__(self, llm: Optional[LLMProvider] = None) -> None:
        validate_generator_prompt_contract()
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

    @staticmethod
    def _response_audit_entry(
        resp: object,
        *,
        attempt: int,
        max_output_tokens: int,
        parse_ok: bool,
    ) -> dict[str, object]:
        """Return non-content diagnostics for one generation response.

        OpenAI-compatible servers normally expose ``finish_reason`` and token
        usage, but lightweight test/legacy providers may expose neither.  A
        response exactly at the requested completion budget is still treated
        as a truncation signal so old Ollama responses remain diagnosable.
        """
        text = str(getattr(resp, "text", "") or "")
        usage = getattr(resp, "usage", None)
        raw_input_tokens = getattr(usage, "input_tokens", None)
        raw_output_tokens = getattr(usage, "output_tokens", None)
        try:
            input_tokens = (
                int(raw_input_tokens) if raw_input_tokens is not None else None
            )
        except (TypeError, ValueError):
            input_tokens = None
        try:
            output_tokens = (
                int(raw_output_tokens) if raw_output_tokens is not None else None
            )
        except (TypeError, ValueError):
            output_tokens = None
        meta = getattr(resp, "meta", None)
        meta = meta if isinstance(meta, dict) else {}
        finish_reason = str(meta.get("finish_reason") or "unavailable")
        token_limit_reached = finish_reason == "length" or (
            output_tokens is not None and output_tokens >= max_output_tokens
        )
        provider_error = str(
            getattr(usage, "error_code", None) or meta.get("error_code") or ""
        ).strip()
        failure_reason: str | None = None
        if not parse_ok:
            if token_limit_reached:
                failure_reason = "output_token_limit_reached"
            elif not text and provider_error:
                failure_reason = f"provider_error:{provider_error}"
            elif not text:
                failure_reason = "empty_response"
            else:
                failure_reason = "invalid_json"
        return {
            "attempt": attempt,
            "parse_ok": parse_ok,
            "failure_reason": failure_reason,
            "finish_reason": finish_reason,
            "token_limit_reached": token_limit_reached,
            "output_chars": len(text),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "max_output_tokens": max_output_tokens,
            "output_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }

    def generate_one(self, req: SynthRequest) -> SynthDoc:
        grade_code = (
            req.target_grade.value
            if hasattr(req.target_grade, "value")
            else str(req.target_grade)
        )
        situation = GRADE_SITUATION_PROMPTS.get(
            grade_code, GRADE_SITUATION_PROMPTS["S3"]
        )
        structure_requirements = req.structure_requirements.strip() or (
            "문서 유형에 자연스러운 여러 절과 항목을 사용하고 각 절에는 서로 다른 사실을 담는다."
        )
        revision_context = req.revision_context.strip()
        if revision_context:
            revision_context = f"[재작성 참고]\n{revision_context}"
        user = USER_TEMPLATE_V2.format(
            situation=req.scenario_context or situation["situation"],
            disclosure_scope=req.disclosure_scope or situation["disclosure_scope"],
            harm_potential=req.harm_potential or situation["harm_potential"],
            domain=req.domain,
            doc_types=req.document_type_hint
            or DOMAIN_DOC_TYPES.get(req.domain, DOMAIN_DOC_TYPES["mixed"]),
            structure_requirements=structure_requirements,
            len_min=req.len_min,
            len_max=req.len_max,
            revision_context=revision_context,
        )
        # C3-7 (2026-05-30): JSON 파싱 실패 시 최대 2회 재시도. Solar 등 JSON 출력
        # 안정성 약한 LLM 에서 76.5% → 30% 미만으로 실패율 감소 기대.
        # 재시도 시 temperature 낮춰 deterministic 시도 + system prompt 강화.
        max_retries = 2
        attempt = 0
        max_output_tokens = max(512, int(req.max_output_tokens))
        response_audit: list[dict[str, object]] = []
        resp = self.llm.generate(
            user,
            system=SYSTEM_PROMPT,
            temperature=0.7,
            max_tokens=max_output_tokens,
        )
        self._record_usage(resp)
        parsed = self._parse(resp.text)
        response_audit.append(
            self._response_audit_entry(
                resp,
                attempt=attempt + 1,
                max_output_tokens=max_output_tokens,
                parse_ok=parsed is not None,
            )
        )
        while parsed is None and attempt < max_retries:
            attempt += 1
            # 재시도: temperature 0.3, system prompt 에 "반드시 유효한 JSON 만 출력" 추가
            retry_system = (
                SYSTEM_PROMPT
                + "\n\n[중요] 반드시 유효한 JSON 객체 1개만 출력하세요. 코드블록·설명·주석 모두 금지."
            )
            resp = self.llm.generate(
                user,
                system=retry_system,
                temperature=0.3,
                max_tokens=max_output_tokens,
            )
            self._record_usage(resp)  # 재시도도 실제 LLM 비용 — 누락 없이 기록
            parsed = self._parse(resp.text)
            response_audit.append(
                self._response_audit_entry(
                    resp,
                    attempt=attempt + 1,
                    max_output_tokens=max_output_tokens,
                    parse_ok=parsed is not None,
                )
            )

        if parsed is None:
            # noop provider 등은 JSON이 아님 — fallback으로 텍스트 그대로 body 사용.
            # [C16] resp.text가 비면 placeholder(_fallback_body)=noop_fallback(학습 금지 마커),
            # 실 LLM이 비-JSON 텍스트를 주면 llm_nonjson — 둘을 label_source로 구분(grep 식별).
            raw_text = resp.text or ""
            body = raw_text or _fallback_body(grade_code, req.domain)
            label_source = "llm_nonjson" if raw_text else "noop_fallback"
            doc_types = req.document_type_hint or DOMAIN_DOC_TYPES.get(
                req.domain, "내부 자료"
            )
            title = (
                f"{doc_types.split(',')[0].strip()} 합성 v{abs(hash(user)) % 10000:04d}"
            )
            doc_type = doc_types.split(",")[0].strip()
            return SynthDoc(
                target_grade=grade_code,
                domain=req.domain,
                title=title,
                body=body,
                document_type=doc_type,
                dept_hint="",
                rationale_tags=[grade_code],
                llm_provider=self.llm.name,
                llm_model=getattr(self.llm, "model", "") or "",
                usage=resp.usage,
                pii_violations=self._pii_violations(body),
                parse_error="non-json response",
                label_source=label_source,
                response_audit=response_audit,
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
            llm_model=getattr(self.llm, "model", "") or "",
            usage=resp.usage,
            pii_violations=self._pii_violations(body),
            response_audit=response_audit,
        )

    def generate(self, req: SynthRequest) -> list[SynthDoc]:
        return [self.generate_one(req) for _ in range(req.count)]


def _fallback_body(grade_code: str, domain: str) -> str:
    """Noop provider / CI 파이프라인 연결 테스트용 fallback.

    실제 품질 평가에 사용하지 않는다 — 이 본문을 쓴 SynthDoc은 label_source="noop_fallback"
    으로 식별된다(학습 편입 금지 마커. build_synthetic_golden은 parse_error로 이미 필터링).

    [등급 누출 차단] 과거엔 GRADE_SITUATION_PROMPTS의 situation/disclosure_scope를 본문에
    직접 넣어, 그 텍스트("외부 공유 절대 불가"·"유출 시 회사 존립…" 등 강한 등급 마커)가 합성
    코퍼스에 누출됐다 — noop_fallback 문서가 학습에 새면 룰·모델이 실데이터에 없는 합성 단서에
    과적합한다. 본문은 **grade-중립 placeholder**로만 둔다(모든 등급에서 동일). 등급은 본문이
    아니라 메타(target_grade)로만 보존되며, grade_code는 호환을 위해 시그니처에만 유지한다.
    """
    del (
        grade_code
    )  # 본문에 등급 신호를 넣지 않는다(누출 차단) — 시그니처는 호출부 호환용.
    return (
        f"[Noop fallback — {domain} 도메인 합성 문서 (파이프라인 연결 테스트)]\n\n"
        "1. 문서 개요\n"
        f"본 자료는 {domain} 도메인의 내부 문서 형식 placeholder이다.\n\n"
        "2. 주요 내용\n"
        "합성 파이프라인 연결 테스트 목적으로 생성된 자리표시 문서로, 등급 판단에 쓰일 "
        "구체 내용(공개 범위·피해 가능성 등)을 담지 않는다.\n\n"
        "3. 결론\n"
        "실제 LLM 호출 시 이 내용은 해당 맥락에 맞는 자연스러운 문서로 대체된다."
    )

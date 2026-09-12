"""M1 SyntheticDocGenerator — LLM Provider 어댑터를 통해 합성 문서 생성.

핵심 변경: build_provider() 사용으로 noop/anthropic/openai/vllm 교체 가능.
Noop provider를 쓰면 결정론적 더미 문서로 파이프라인 검증 가능.

──────────────────────────────────────────────────────────────────────────────
합성을 어디에 쓰는가 (2026-09-05 결정)

합성을 **양**으로 늘리는 길은 실측으로 막혀 있다: 합성-only 로 학습해 실문서를 재면
F1 0.26(실데이터 학습 0.736). 많이 만들어도 실문서 성능이 그만큼 오르지 않는다.
그래서 용도를 셋으로 좁힌다.

  ① 빈 칸 채우기   실데이터로 못 얻는 등급×도메인 조합만 지목해 만든다.
                   빈 칸은 scripts/synth_coverage_gaps.py 로 센다(무작위 증량 금지).
  ② 회귀 탐침      정답을 우리가 아는 유일한 데이터다. "보안표시를 올리면 등급이
                   안 내려간다" 같은 불변식 검사에는 실문서보다 낫다. F1 천장과 무관.
  ③ 시연·요건 이행  화면은 요건(FUN-003-⑦)이라 유지한다.

병목은 생성이 아니라 검수다 — 223 실서버 실측: 40건 만들어 승인 2건
(rejected 22 · pending_review 16 · approved 2). 생성량을 늘리면 pending 만 쌓인다.

품질 규율
  · 누출 게이트가 생성 직후·검수큐 적재 **전**에 돈다(services/synth_quality.screen_batch,
    workers/tasks.synthesize_batch 에서 호출). 옛 산출물의 33.8%가 본문에 자기 등급명을
    노출한 채 쌓였고(S1 94.3%) 그것을 학습셋 단계에서야 걸러냈다 — 검수자가 답이 적힌
    문서를 읽으면 검수가 검증이 아니라 확인 절차가 된다.
  · 아래 GRADE_KEYWORDS 는 룰 라벨러(M3) 전용이다. **프롬프트에 주입하지 않는다.**
    V2 는 등급명 대신 상황·맥락으로 유도한다(GRADE_SITUATION_PROMPTS).

도메인 어휘
  DOMAIN_DOC_TYPES 가 정본이고 API 스키마(schemas/synthesis.py)가 여기서 파생한다.
  손으로 적어 두었더니 어긋났다 — API 가 6개만 받아 정작 얇은 칸(TS ai·semiconductor·
  defense)을 거부했다(2026-09-05 수정). 도메인을 늘리면 여기에만 추가하면 된다.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from koipa.adapters.llm import build_provider
from koipa.adapters.llm.base import LLMProvider, UsageRecord, accepts_json_schema


logger = logging.getLogger(__name__)


def _settings():
    """설정을 호출 시점에 읽는다 — import 시점에 고정하면 시험이 못 바꾼다."""
    from koipa.config import settings  # noqa: PLC0415

    return settings


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

# [2026-09-05] 프롬프트가 금지하는 등급 어휘 — **게이트가 이 목록을 읽는다.**
#
# 종전에는 이 목록이 프롬프트 문자열 안에만 있었고, 검사하는 쪽
# (synth_quality._exposes_grade_token)은 dataset_leakage._GRADE_TOKEN 을 썼다. 그것은
# \b(TS|S1|S2|S3)\b 뿐이라 "1급 비밀"·"대외비"·"Level 1 Secret" 을 하나도 못 잡는다.
# 실측(rag_corpus_v2 720건): 실제 등급표현이 있는 문서 527건 중 게이트가 잡은 것은 192건 —
# **377건(52.4%)이 답을 적은 채로 검수 후보에 들어갔다.** 검수 후보에서 등급 노출은
# 0 이 기준인데(allow_grade_token=False) 절반이 통과한 것이다.
#
# 프롬프트와 게이트가 같은 목록을 보게 해서 다시 갈라지지 않게 한다.
#
# ⚠ 이 목록을 dataset_leakage._GRADE_TOKEN 으로 합치지 말 것. 그쪽은 실문서가 섞인
#   학습셋에도 돌고, 실문서에 찍힌 "대외비" 는 **비밀관리성(M)의 근거**다
#   (rule_engine._MANAGEMENT_MARKING_TERMS 가 점수로 쓴다). 생성물에서만 금지다.
FORBIDDEN_GRADE_TERMS: tuple[str, ...] = (
    "TS", "S1", "S2", "S3",
    "특급기밀", "특급 기밀", "1급 비밀", "1급비밀", "2급 비밀", "2급비밀",
    "3급 비밀", "3급비밀", "대외비", "극비", "사외비",
    "Top Secret", "Level 1 Secret", "Level 2 Secret", "Level 3 Secret",
    "Confidential Material",
)


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

# ──────────────────────────────────────────────────────────────────────────────
# 구조화 출력 스키마 — 서버가 이 틀 밖의 토큰을 만들지 못하게 한다.
#
# 종전에는 SYSTEM_PROMPT 에 "출력은 반드시 다음 JSON 한 객체" 라고 **부탁만** 했고,
# 모델이 인사말이나 코드펜스를 덧붙이면 _parse 가 실패해 재시도했다(최대 2회 추가 호출).
# 스키마를 넘길 수 있는 provider 면 그 재시도가 필요 없어진다.
#
# ⚠ 프롬프트의 JSON 서술과 이 스키마는 **같은 필드 집합**이어야 한다. 갈라지면 스키마를
#   받는 서버와 못 받는 서버가 서로 다른 모양을 돌려준다. 시험이 이 일치를 지킨다
#   (test_synth_structured_output.py).
SYNTH_DOC_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "document_type": {"type": "string"},
        "dept_hint": {"type": "string"},
        "rationale_tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "body", "document_type", "dept_hint", "rationale_tags"],
    "additionalProperties": False,
}

OUTLINE_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "document_type": {"type": "string"},
        "dept_hint": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "intent": {"type": "string"},
                },
                "required": ["heading", "intent"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "document_type", "dept_hint", "sections"],
    "additionalProperties": False,
}

CRITIQUE_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["issues"],
    "additionalProperties": False,
}


# ──────────────────────────────────────────────────────────────────────────────
# 다단계 생성 프롬프트 — 개요 → 본문 → 자체검토 → 수정
#
# 한 번의 호출로 "제목 정하고 목차 짜고 2,000자 본문까지" 를 시키면 뒤로 갈수록 절이
# 비거나 같은 말이 반복된다. 사람이 초안 없이 최종본을 쓰는 것과 같다.
#
# 본문 단계는 **새 템플릿을 쓰지 않는다** — 기존 USER_TEMPLATE_V2 의 [구조·완성도 요구]
# 칸에 개요를 넣고, 수정 단계는 [재작성 참고](revision_context) 칸에 지적사항을 넣는다.
# 두 칸 다 이미 SynthRequest 에 있었는데 아무도 채우지 않고 있었다.
OUTLINE_SYSTEM_PROMPT = """당신은 한국 조직의 사내 문서를 설계하는 문서 기획자다.
본문을 쓰지 말고, 어떤 절을 어떤 의도로 둘지만 정한다.

[준수]
- 실재 기업명·인명·연락처 등 PII 금지. 사람은 [공정책임자A] 처럼 역할 식별자만 쓴다.
- 절은 해당 문서 유형에 실제로 있는 것만 둔다. 장식용 절을 만들지 않는다.
- 각 절의 의도는 서로 겹치지 않아야 한다. 같은 내용을 두 절에 나누어 담지 않는다.
- 등급명(TS, S1, S2, S3, 특급기밀, 1급 비밀, 2급, 3급, 대외비, 기밀, 비밀, 극비) 기재 금지.
- 출력은 다음 JSON 한 객체뿐이며 설명·코드펜스를 붙이지 않는다:
  {"title": str, "document_type": str, "dept_hint": str,
   "sections": [{"heading": str, "intent": str}, ...]}"""

OUTLINE_TEMPLATE = """[문서 상황]
{situation}

[공개 범위]
{disclosure_scope}

[잠재적 피해 가능성]
{harm_potential}

[도메인]
{domain}

[문서 유형 후보]
{doc_types}

[분량 감각]
본문 전체가 한국어 {len_min}자 이상 {len_max}자 이내가 되도록 절 수를 정한다.
절이 너무 많으면 각 절이 비고, 너무 적으면 한 절이 길어진다.

위 상황에 실재할 법한 사내 문서의 절 구성을 JSON 객체로 작성하시오.
절은 3개 이상 7개 이하로 하고, 각 절의 의도를 한 문장으로 적으시오."""

CRITIQUE_SYSTEM_PROMPT = """당신은 사내 문서를 검토하는 감사 담당자다.
주어진 문서에서 고쳐야 할 점만 찾아 적는다. 문서를 다시 쓰지 않는다.

[무엇을 찾는가]
- 절 제목만 있고 내용이 비었거나, 같은 사실을 표현만 바꿔 반복한 곳
- 수치·날짜·단위의 산술 모순, 시간 순서 모순, 표와 본문의 값 불일치
- 표의 빈 칸이나 하이픈 placeholder, 표로 끝나고 결론·후속조치가 없는 구성
- 실재 기업명·인명·주민등록번호·연락처·이메일 등 PII
- 등급명(TS, S1, S2, S3, 특급기밀, 1급 비밀, 2급, 3급, 대외비, 기밀, 비밀, 극비) 표기

[지적하면 안 되는 것]
- [공정책임자A]·[검토자B] 같은 대괄호 역할 식별자와 품질관리팀·기획팀 같은 부서명은
  **작성 규칙이 요구한 표기**다. 실명 대신 쓰라고 지시한 것이므로 PII 가 아니다.
- [가상기업N] 같은 자리표시자도 마찬가지다.

[준수]
- 고칠 것이 없으면 빈 배열을 돌려준다. 없는 문제를 만들지 않는다.
- 각 지적은 어느 절의 무엇을 어떻게 고치라는 것인지 한 문장으로 적는다.
- 출력은 다음 JSON 한 객체뿐이며 설명·코드펜스를 붙이지 않는다:
  {"issues": [str, ...]}"""

CRITIQUE_TEMPLATE = """[검토 대상 문서]
제목: {title}

{body}

위 문서에서 고쳐야 할 점을 JSON 객체로 적으시오. 없으면 issues 를 빈 배열로 두시오."""


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
    # [2026-09-05] 학습셋에 **실재하는데 생성기가 모르던** 한국 산업 도메인 — 944행(37.0%).
    # 이 칸들은 어휘를 통일해도 문서 유형을 모르면 채울 수 없었다.
    "배터리": "양극재 조성 설계서, 셀 공정 레시피, 전해액 배합 연구노트, 수명 시험 성적서",
    "반도체": "공정 레시피 명세, 노광 파라미터 설계서, 수율 개선 연구노트, 소자 설계 도면",
    "화학_제약": "합성 경로 연구노트, 원료 배합비 명세, 임상 프로토콜, 제형 안정성 시험서",
    "바이오_농업": "품종 육성 기록, 유전자원 관리 대장, 재배 시험 보고서, 종자 처리 공정서",
    "소프트웨어": "아키텍처 설계서, API 명세, 릴리스 노트, 장애 사후분석 보고서",
    "경영정보": "경영 실적 보고, 조직 개편안, 예산 배분 계획, 이사회 안건서",
    "기타": "내부 공지, 업무 협조전, 교육 자료, 절차 안내서",
}


# [2026-09-05] 어휘 통일 — 같은 산업이 영문·한글 두 칸으로 갈려 있었다.
#
# 학습셋 실측: semiconductor 1 vs 반도체 159 · pharma 2 vs 화학_제약 102 ·
# battery 2 vs 배터리 29 · bio 0 vs 바이오_농업 9. 갈림 때문에 커버리지 빈 칸 9개·
# 얇은 칸 4개가 없던 문제로 부풀려졌다(TS battery 2 + 배터리 12 = 14 — 합치면 얇지 않다).
#
# **한글을 정본으로 삼는다** — 행 수가 압도적으로 많고(159 대 1), 학습셋 실물이 그 이름을 쓴다.
# 영문 이름은 계속 받되 한글로 접어 넣는다(옛 요청·옛 데이터가 깨지지 않게).
# ⚠ 접을 수 있는 것은 **같은 산업**을 두 이름으로 부르던 경우뿐이다.
#
# [2026-09-05 정정] bio -> 바이오_농업 은 그 조건을 만족하지 않아 걷어냈다. 두 항목의
# 문서 유형이 서로 다른 산업이다:
#     bio          신약 후보물질 연구노트 · 임상시험 프로토콜 · 화합물 합성 경로 · FDA 전략 기획서
#     바이오_농업   품종 육성 기록 · 유전자원 관리 대장 · 재배 시험 보고서 · 종자 처리 공정서
# canonical_domain() 이 프롬프트 직전(generate_one)에 적용되므로, 이 접기는 집계뿐 아니라
# **생성 내용까지** 바꿨다 — 콘솔에서 「bio (바이오·제약)」을 고르면 종자·품종 문서가
# 나왔다. golden50/golden500 도 TS·S1 도메인으로 bio 를 쓰는데 같은 것을 받고 있었다.
# 제약 문서의 정본은 화학_제약 이고 pharma 로 접는다. 그리고 위 실측에서 bio 행은 0건이라
# 이 별칭을 걷어도 합칠 것이 없다 — 나머지 셋과 모양을 맞추다 들어간 항목이었다.
DOMAIN_ALIASES = {
    "semiconductor": "반도체",
    "battery": "배터리",
    "pharma": "화학_제약",
}


def canonical_domain(name: str | None) -> str:
    """도메인 이름을 정본으로 접는다. 모르는 이름은 그대로 둔다(거부는 스키마가 한다)."""
    key = (name or "").strip()
    return DOMAIN_ALIASES.get(key, key) or "mixed"


# [2026-09-05] 프롬프트 버전 — 재현·감사 앵커.
#
# tad_sm_syn_doc_mng 의 *_prompt_version 세 칸이 늘 비어 있었다. 채울 값이 없었기 때문이다.
# 프롬프트를 고쳐도 "어느 프롬프트로 만든 문서인가"를 되짚을 수 없었고, 그래서 품질이
# 갈렸을 때 원인을 프롬프트로 좁힐 수 없었다.
#
# 사람이 올리는 번호 대신 **내용 해시**를 쓴다. 올리는 것을 잊어도 내용이 바뀌면 값이 바뀐다.
# 형식: "v2-<sha256 앞 8자>" — v2 는 등급명 대신 상황으로 유도하는 현행 프롬프트 세대다.


def _prompt_version(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").encode("utf-8"))
        digest.update(b"\0")
    return "v2-" + digest.hexdigest()[:8]


def outline_prompt_version() -> str:
    """개요 프롬프트 버전.

    [2026-09-10] 종전에는 본문 버전을 그대로 돌려줬다 — 개요·본문이 한 호출이었기
    때문이다. 다단계 생성이 생기면서 개요는 자기 프롬프트를 갖게 됐고, 이제 실제로
    개요 프롬프트의 해시를 돌려준다. 단계를 쓰지 않은 문서(단발 생성)는 개요 프롬프트를
    거치지 않았으므로 호출부가 본문 버전만 기록한다(workers/tasks.py).
    """
    return _prompt_version(OUTLINE_SYSTEM_PROMPT, OUTLINE_TEMPLATE)


def critique_prompt_version() -> str:
    """자체검토 프롬프트 버전 — 다단계 생성에서만 쓰인다."""
    return _prompt_version(CRITIQUE_SYSTEM_PROMPT, CRITIQUE_TEMPLATE)


def body_prompt_version() -> str:
    """본문 프롬프트 버전 — 시스템 프롬프트 + 등급 상황문 전체의 해시."""
    situ = json.dumps(GRADE_SITUATION_PROMPTS, ensure_ascii=False, sort_keys=True)
    doct = json.dumps(DOMAIN_DOC_TYPES, ensure_ascii=False, sort_keys=True)
    return _prompt_version(SYSTEM_PROMPT, situ, doct)


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
    # 다단계 생성 프롬프트도 같은 무결성 게이트를 받는다 — 인코딩이 깨진 채로 서버에
    # 나가면 개요 단계가 조용히 쓰레기를 만들고 본문이 그것을 따라 쓴다.
    _require_korean_prompt_text(
        OUTLINE_SYSTEM_PROMPT, field="OUTLINE_SYSTEM_PROMPT", min_hangul=50
    )
    _require_korean_prompt_text(
        OUTLINE_TEMPLATE, field="OUTLINE_TEMPLATE", min_hangul=50
    )
    _require_korean_prompt_text(
        CRITIQUE_SYSTEM_PROMPT, field="CRITIQUE_SYSTEM_PROMPT", min_hangul=50
    )
    _require_korean_prompt_text(
        CRITIQUE_TEMPLATE, field="CRITIQUE_TEMPLATE", min_hangul=20
    )
    for grade, fields in GRADE_SITUATION_PROMPTS.items():
        for prompt_field in ("situation", "disclosure_scope", "harm_potential"):
            _require_korean_prompt_text(
                fields.get(prompt_field),
                field=f"GRADE_SITUATION_PROMPTS[{grade!r}].{prompt_field}",
            )
    for domain, value in DOMAIN_DOC_TYPES.items():
        _require_korean_prompt_text(value, field=f"DOMAIN_DOC_TYPES[{domain!r}]")


# 주민등록번호 패턴 — 마스커(m2_preprocess/pii_masker.py)와 같은 기준을 쓴다.
# 7번째 자리를 [1-4] 로 제약하고 법인등록번호는 corp_reg 로 따로 둔다. 앞뒤 숫자
# lookaround 로 계좌·일련번호 부분 매치를 막는다.
# 종전 6자리-7자리 패턴은 법인등록번호까지 PII 위반으로 셌다 — 학습셋 합성 3,187행에서
# 잡힌 위반 2건이 둘 다 법인등록번호였고 실제 PII 위반은 0건이었다(실측 2026-08-24).
_PII_PATTERNS = [
    re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"),   # 주민등록번호
    re.compile(r"(?<!\d)\d{6}[- ]?[5-8]\d{6}(?!\d)"),   # 외국인등록번호
    re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"),     # 휴대전화
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # 이메일
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
    # [2026-09-10] 다단계 생성(개요→본문→자체검토→수정) 사용 여부.
    # None = 설정값(settings.synth_multi_step)을 따른다. True/False = 이 요청만 강제.
    # 켜면 문서 1건당 LLM 호출이 1회에서 3~4회로 는다.
    multi_step: Optional[bool] = None


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
    # [2026-09-10] "single" = 종전과 같은 1회 호출. "multi_step" = 개요→본문→검토→수정.
    # 어느 쪽으로 만든 문서인지 뒤에서 갈라 재려면 산출물에 남아 있어야 한다.
    generation_mode: str = "single"
    # 자체검토가 실제로 무엇을 지적했는지. 빈 리스트는 "지적 없음"이고, 단발 생성이면
    # 애초에 검토를 안 했으므로 역시 빈 리스트다 — 둘을 가르는 것은 generation_mode 다.
    critique_issues: list[str] = field(default_factory=list)


class SyntheticDocGenerator:
    """합성 문서 생성기.

    [2026-09-10] 세 가지가 붙었다. 셋 다 프레임워크 없이 provider 어댑터 위에서 돈다.
      ① 구조화 출력   서버가 JSON 스키마 밖 토큰을 못 만들게 한다(지원 서버일 때만).
      ② 병렬 배치     count>1 을 동시에 만든다. 기본값 1 = 종전과 같은 순차 생성.
      ③ 다단계 생성   개요→본문→자체검토→수정. 기본 꺼짐 = 종전과 같은 1회 호출.
    """

    # JSON 파싱 실패 시 추가 호출 횟수. 종전과 같은 2회를 유지한다 — 구조화 출력이
    # 붙었다고 재시도를 늘리면 실패 비용이 조용히 커진다.
    _MAX_JSON_RETRIES = 2

    def __init__(
        self,
        llm: Optional[LLMProvider] = None,
        *,
        concurrency: Optional[int] = None,
        structured_output: Optional[bool] = None,
        multi_step: Optional[bool] = None,
    ) -> None:
        validate_generator_prompt_contract()
        self.llm = llm or build_provider()
        self.concurrency = self._resolve_concurrency(concurrency)
        # 구조화 출력은 **설정이 켜져 있고 provider 가 인자를 받을 때만** 쓴다.
        # 둘 중 하나만 봐도 안 된다: 설정만 보면 anthropic 에서 TypeError 가 나고,
        # provider 만 보면 설정으로 끌 수가 없다.
        want_structured = (
            structured_output
            if structured_output is not None
            else bool(getattr(_settings(), "synth_structured_output", True))
        )
        self.structured_output = bool(want_structured) and accepts_json_schema(self.llm)
        self.multi_step = (
            bool(multi_step)
            if multi_step is not None
            else bool(getattr(_settings(), "synth_multi_step", False))
        )

    @staticmethod
    def _resolve_concurrency(value: Optional[int]) -> int:
        raw = value if value is not None else getattr(
            _settings(), "synth_generate_concurrency", 1
        )
        try:
            resolved = int(raw)
        except (TypeError, ValueError):
            resolved = 1
        # 0·음수가 오면 아무것도 만들지 않거나 죽는다 — 조용한 0건 생성을 막는다.
        return max(1, min(32, resolved))

    @staticmethod
    def _record_usage(resp: object) -> None:
        """[QW] 합성 LLM 호출 비용 best-effort 기록 (purpose='synthesis')."""
        try:
            from koipa.services.llm_usage_service import record_llm_usage  # noqa: PLC0415

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
        step: str = "body",
        json_schema: bool = False,
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
            # 어느 단계의 호출인가(body·outline·critique·revise). 다단계 생성에서 한 문서가
            # 여러 호출을 쓰므로 단계 표시가 없으면 감사 기록을 되짚을 수 없다.
            "step": step,
            # 이 호출에 JSON 스키마를 걸었는가. 실패 후 재시도는 스키마를 떼므로,
            # "스키마를 걸었더니 실패했다" 와 "떼니 됐다" 가 이 값으로 갈린다.
            "json_schema": json_schema,
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

    # ── 프롬프트 조립 ────────────────────────────────────────────────────────
    def _build_user_prompt(
        self,
        req: SynthRequest,
        grade_code: str,
        req_domain: str,
        *,
        structure_override: str = "",
        revision_override: str = "",
        doc_type_override: str = "",
    ) -> str:
        """본문 프롬프트 1개를 만든다.

        override 세 개는 다단계 생성이 쓴다 — 개요 단계가 정한 절 구성이
        [구조·완성도 요구] 칸으로, 자체검토가 낸 지적이 [재작성 참고] 칸으로 들어간다.
        단발 생성에서는 전부 빈 문자열이라 종전과 같은 프롬프트가 나온다.
        """
        situation = GRADE_SITUATION_PROMPTS.get(
            grade_code, GRADE_SITUATION_PROMPTS["S3"]
        )
        structure_requirements = (
            structure_override.strip()
            or req.structure_requirements.strip()
            or "문서 유형에 자연스러운 여러 절과 항목을 사용하고 각 절에는 서로 다른 사실을 담는다."
        )
        revision_context = (revision_override or req.revision_context).strip()
        if revision_context:
            revision_context = f"[재작성 참고]\n{revision_context}"
        doc_types = (
            doc_type_override.strip()
            or req.document_type_hint
            or DOMAIN_DOC_TYPES.get(req_domain, DOMAIN_DOC_TYPES["mixed"])
        )
        return USER_TEMPLATE_V2.format(
            situation=req.scenario_context or situation["situation"],
            disclosure_scope=req.disclosure_scope or situation["disclosure_scope"],
            harm_potential=req.harm_potential or situation["harm_potential"],
            domain=req_domain,
            doc_types=doc_types,
            structure_requirements=structure_requirements,
            len_min=req.len_min,
            len_max=req.len_max,
            revision_context=revision_context,
        )

    # ── LLM 호출 1건(JSON 응답) ──────────────────────────────────────────────
    def _call_json(
        self,
        user: str,
        *,
        system: str,
        schema: Optional[dict],
        max_output_tokens: int,
        step: str,
        audit: list[dict[str, object]],
    ) -> tuple[dict | None, object]:
        """JSON 한 객체를 받아 낸다. 총 호출 횟수는 최대 1 + _MAX_JSON_RETRIES.

        시도 순서
          ① 스키마를 걸고 temperature 0.7 — 지원 provider + 설정 켜짐일 때만.
          ② 스키마를 떼고 temperature 0.3 + "JSON 만" 강화 시스템 프롬프트.

        재시도에서 **스키마를 떼는 것이 핵심**이다. ①이 실패하는 원인 중 하나가
        "서버가 response_format 을 못 받는다" 이기 때문이다(구형 vLLM·Ollama).
        스키마를 그대로 두고 재시도하면 같은 이유로 세 번 다 실패한다.
        """
        schema_used = bool(schema) and self.structured_output
        extra = {"json_schema": schema} if schema_used else {}
        resp = self.llm.generate(
            user,
            system=system,
            temperature=0.7,
            max_tokens=max_output_tokens,
            **extra,
        )
        self._record_usage(resp)
        parsed = self._parse(resp.text)
        audit.append(
            self._response_audit_entry(
                resp,
                attempt=1,
                max_output_tokens=max_output_tokens,
                parse_ok=parsed is not None,
                step=step,
                json_schema=schema_used,
            )
        )
        attempt = 1
        while parsed is None and attempt <= self._MAX_JSON_RETRIES:
            attempt += 1
            retry_system = (
                system
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
            audit.append(
                self._response_audit_entry(
                    resp,
                    attempt=attempt,
                    max_output_tokens=max_output_tokens,
                    parse_ok=parsed is not None,
                    step=step,
                    json_schema=False,
                )
            )
        return parsed, resp

    # ── 단발 생성(종전 경로) ─────────────────────────────────────────────────
    def _generate_single(
        self,
        req: SynthRequest,
        grade_code: str,
        req_domain: str,
        *,
        structure_override: str = "",
        revision_override: str = "",
        doc_type_override: str = "",
        audit: Optional[list[dict[str, object]]] = None,
        step: str = "body",
        generation_mode: str = "single",
    ) -> SynthDoc:
        response_audit: list[dict[str, object]] = audit if audit is not None else []
        max_output_tokens = max(512, int(req.max_output_tokens))
        user = self._build_user_prompt(
            req,
            grade_code,
            req_domain,
            structure_override=structure_override,
            revision_override=revision_override,
            doc_type_override=doc_type_override,
        )
        parsed, resp = self._call_json(
            user,
            system=SYSTEM_PROMPT,
            schema=SYNTH_DOC_JSON_SCHEMA,
            max_output_tokens=max_output_tokens,
            step=step,
            audit=response_audit,
        )

        if parsed is None:
            # noop provider 등은 JSON이 아님 — fallback으로 텍스트 그대로 body 사용.
            # [C16] resp.text가 비면 placeholder(_fallback_body)=noop_fallback(학습 금지 마커),
            # 실 LLM이 비-JSON 텍스트를 주면 llm_nonjson — 둘을 label_source로 구분(grep 식별).
            raw_text = getattr(resp, "text", "") or ""
            body = raw_text or _fallback_body(grade_code, req_domain)
            label_source = "llm_nonjson" if raw_text else "noop_fallback"
            doc_types = (
                doc_type_override.strip()
                or req.document_type_hint
                or DOMAIN_DOC_TYPES.get(req_domain, "내부 자료")
            )
            title = (
                f"{doc_types.split(',')[0].strip()} 합성 v{abs(hash(user)) % 10000:04d}"
            )
            doc_type = doc_types.split(",")[0].strip()
            return SynthDoc(
                target_grade=grade_code,
                domain=req_domain,
                title=title,
                body=body,
                document_type=doc_type,
                dept_hint="",
                rationale_tags=[grade_code],
                llm_provider=self.llm.name,
                llm_model=getattr(self.llm, "model", "") or "",
                usage=getattr(resp, "usage", None),
                pii_violations=self._pii_violations(body),
                parse_error="non-json response",
                label_source=label_source,
                response_audit=response_audit,
                generation_mode=generation_mode,
            )

        body = parsed.get("body", "") or ""
        return SynthDoc(
            target_grade=grade_code,
            domain=req_domain,
            title=parsed.get("title", "") or "",
            body=body,
            document_type=parsed.get("document_type", "") or "",
            dept_hint=parsed.get("dept_hint", "") or "",
            rationale_tags=list(parsed.get("rationale_tags", []) or []),
            llm_provider=self.llm.name,
            llm_model=getattr(self.llm, "model", "") or "",
            usage=getattr(resp, "usage", None),
            pii_violations=self._pii_violations(body),
            response_audit=response_audit,
            generation_mode=generation_mode,
        )

    # ── 다단계 생성 ──────────────────────────────────────────────────────────
    @staticmethod
    def _outline_sections(parsed: object) -> list[dict]:
        """개요 응답에서 쓸 수 있는 절 목록만 골라 낸다.

        JSON 파싱이 됐다고 개요가 온 것은 아니다 — noop provider 는 어떤 프롬프트에도
        같은 문서 JSON(sections 없음)을 돌려준다. 여기서 걸러야 그 응답을 개요로 착각해
        빈 구조를 본문 프롬프트에 밀어 넣지 않는다.
        """
        if not isinstance(parsed, dict):
            return []
        sections = parsed.get("sections")
        if not isinstance(sections, list):
            return []
        out = []
        for item in sections:
            if not isinstance(item, dict):
                continue
            heading = str(item.get("heading", "") or "").strip()
            if not heading:
                continue
            out.append(
                {"heading": heading, "intent": str(item.get("intent", "") or "").strip()}
            )
        return out

    def _generate_multi_step(
        self, req: SynthRequest, grade_code: str, req_domain: str
    ) -> SynthDoc:
        """개요 → 본문 → 자체검토 → (지적이 있으면) 수정.

        어느 단계가 실패하든 **종전 단발 생성으로 내려간다.** 다단계를 켰다고 생성이
        0건이 되면 안 된다 — 실패는 response_audit 의 step 값으로 남는다.
        """
        audit: list[dict[str, object]] = []
        max_output_tokens = max(512, int(req.max_output_tokens))
        situation = GRADE_SITUATION_PROMPTS.get(
            grade_code, GRADE_SITUATION_PROMPTS["S3"]
        )
        outline_user = OUTLINE_TEMPLATE.format(
            situation=req.scenario_context or situation["situation"],
            disclosure_scope=req.disclosure_scope or situation["disclosure_scope"],
            harm_potential=req.harm_potential or situation["harm_potential"],
            domain=req_domain,
            doc_types=req.document_type_hint
            or DOMAIN_DOC_TYPES.get(req_domain, DOMAIN_DOC_TYPES["mixed"]),
            len_min=req.len_min,
            len_max=req.len_max,
        )
        outline_parsed, _ = self._call_json(
            outline_user,
            system=OUTLINE_SYSTEM_PROMPT,
            schema=OUTLINE_JSON_SCHEMA,
            max_output_tokens=min(max_output_tokens, 1200),
            step="outline",
            audit=audit,
        )
        sections = self._outline_sections(outline_parsed)
        if not sections:
            logger.info(
                "multi-step: 개요 단계가 쓸 만한 절 구성을 못 냈다 — 단발 생성으로 내려간다"
            )
            return self._generate_single(
                req, grade_code, req_domain, audit=audit, generation_mode="multi_step"
            )

        structure = (
            "다음 절 구성을 그대로 따르고 절 제목을 본문에 그대로 쓴다.\n"
            + "\n".join(
                f"{i}) {sec['heading']} — {sec['intent']}"
                for i, sec in enumerate(sections, start=1)
            )
        )
        doc_type_override = ""
        if isinstance(outline_parsed, dict):
            doc_type_override = str(
                outline_parsed.get("document_type", "") or ""
            ).strip()

        doc = self._generate_single(
            req,
            grade_code,
            req_domain,
            structure_override=structure,
            doc_type_override=doc_type_override,
            audit=audit,
            step="body",
            generation_mode="multi_step",
        )
        if doc.parse_error or not doc.body:
            return doc

        critique_parsed, _ = self._call_json(
            CRITIQUE_TEMPLATE.format(title=doc.title, body=doc.body),
            system=CRITIQUE_SYSTEM_PROMPT,
            schema=CRITIQUE_JSON_SCHEMA,
            max_output_tokens=min(max_output_tokens, 1200),
            step="critique",
            audit=audit,
        )
        issues: list[str] = []
        if isinstance(critique_parsed, dict):
            raw_issues = critique_parsed.get("issues")
            if isinstance(raw_issues, list):
                issues = [str(x).strip() for x in raw_issues if str(x).strip()]
        if not issues:
            doc.critique_issues = []
            return doc

        revised = self._generate_single(
            req,
            grade_code,
            req_domain,
            structure_override=structure,
            revision_override="\n".join(f"- {issue}" for issue in issues),
            doc_type_override=doc_type_override,
            audit=audit,
            step="revise",
            generation_mode="multi_step",
        )
        # 수정본이 깨졌으면 검토 전 본문을 쓴다 — 검토가 결과를 나쁘게 만들면 안 된다.
        if revised.parse_error or not revised.body:
            doc.critique_issues = issues
            return doc
        revised.critique_issues = issues
        return revised

    def generate_one(self, req: SynthRequest) -> SynthDoc:
        grade_code = (
            req.target_grade.value
            if hasattr(req.target_grade, "value")
            else str(req.target_grade)
        )
        req_domain = canonical_domain(req.domain)
        multi = self.multi_step if req.multi_step is None else bool(req.multi_step)
        if multi:
            return self._generate_multi_step(req, grade_code, req_domain)
        return self._generate_single(req, grade_code, req_domain)

    def generate(self, req: SynthRequest) -> list[SynthDoc]:
        """count 건을 만든다.

        concurrency 가 1 이면 종전과 완전히 같은 순차 생성이다. 2 이상이면 그만큼을
        동시에 보낸다 — **순서는 유지된다**(executor.map). 순서가 흔들리면 같은 요청을
        두 번 돌렸을 때 산출물 비교가 안 된다.

        ⚠ 동시성을 올린다고 늘 빨라지지 않는다. GPU 1장에 모델 서버가 겹쳐 있으면
        오히려 느려진다(2026-09-10 211 실측: vLLM·Ollama 동거 시 0.78 tokens/sec).
        올리기 전에 그 서버에서 건당 소요를 먼저 잰다.
        """
        count = max(0, int(req.count))
        if count == 0:
            return []
        workers = min(self.concurrency, count)
        if workers <= 1:
            return [self.generate_one(req) for _ in range(count)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda _: self.generate_one(req), range(count)))


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

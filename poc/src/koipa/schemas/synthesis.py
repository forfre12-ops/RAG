"""Synthesis 도메인 스키마 (OpenAPI /synth/*)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from koipa.modules.m1_synthesis.generator import DOMAIN_ALIASES, DOMAIN_DOC_TYPES

from .common import Actor, Grade

# [2026-09-05] 생성기 어휘에서 파생한다 — 손으로 적어 두었더니 어긋났다.
#
# 실측: 생성기는 13개 도메인의 프롬프트를 갖고 있는데 API 정규식은 6개
# (tech·business·hr·finance·legal·mixed)만 받고 있었다. 그 6개는 학습셋에서 이미
# 등급별 60건 이상으로 채워진 칸이고, 정작 얇은 칸(TS ai 1건·TS semiconductor 1건·
# TS defense 2건)의 도메인은 생성기가 프롬프트를 갖고 있는데도 API 가 거부했다.
# 즉 **생성 능력이 필요 없는 칸에만 열려 있었다.**
#
# 빈 칸은 scripts/synth_coverage_gaps.py 로 센다.
# [2026-09-05] 별칭도 받는다 — 옛 요청(semiconductor·battery·pharma·bio)이 깨지지 않게.
# 생성기가 canonical_domain() 으로 정본(한글)에 접어 넣는다.
_SYNTH_DOMAINS = sorted(set(DOMAIN_DOC_TYPES) | set(DOMAIN_ALIASES))
SYNTH_DOMAIN_PATTERN = r"^(" + "|".join(_SYNTH_DOMAINS) + r")$"

# [2026-09-05] LLM provider 목록도 **정본에서 파생**한다.
#
# 종전에는 여기 손으로 적어 두었고 팩토리와 갈렸다 — API 는 vllm_qwen·vllm_exaone 을 받는데
# build_provider() 는 그 이름을 모르고(ValueError), 팩토리가 아는 ollama·local_openai·
# lm_studio·vllm 은 API 로 넣을 수 없었다. 도메인 목록에서 같은 실수를 한 뒤라 같은 방식으로
# 막는다 — 정본은 config._VALID_LLM_PROVIDER 이고 여기서 파생한다.
from koipa.config import _VALID_LLM_PROVIDER as _LLM_PROVIDERS  # noqa: E402

LLM_PROVIDER_PATTERN = r'^(' + '|'.join(sorted(_LLM_PROVIDERS)) + r')$'


class SynthGenerateRequest(BaseModel):
    target_grade: Grade
    domain: str = Field(default="mixed", pattern=SYNTH_DOMAIN_PATTERN)
    count: int = Field(ge=1, le=500)
    llm_provider: str = Field(default="anthropic", pattern=LLM_PROVIDER_PATTERN)
    actor: Actor


class SynthGenerateResponse(BaseModel):
    synth_job_id: UUID
    expected_count: int
    estimated_cost_usd: float
    # [2026-09-05] 발사 여부를 응답에 담는다. 종전에는 브로커가 없거나 발사에 실패해도
    # 202 만 돌려줘 **호출자가 "등록만 되고 생성은 안 됨"을 알 수 없었다**(로그에만 남았다).
    #   dispatched=True   워커로 발사됨 — 생성이 진행된다
    #   dispatched=False  등록만 됨 — 브로커 미가용이거나 발사 실패. 생성은 일어나지 않는다
    dispatched: bool = False
    dispatch_note: str | None = None


class SyntheticDocItem(BaseModel):
    synth_id: UUID
    target_grade: Grade
    domain: Optional[str] = None
    llm_provider: str
    llm_model: str
    quality_score: Optional[float] = None
    review_status: str
    # 검수자가 승인하면서 고친 등급. None=교정 없음(target_grade 가 그대로 학습 라벨).
    corrected_grade: Optional[Grade] = None
    preview: Optional[str] = Field(default=None, max_length=2000)
    created_at: Optional[str] = None


class SynthQueueResponse(BaseModel):
    total: int
    items: list[SyntheticDocItem]


class SynthReviewRequest(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    corrected_grade: Optional[Grade] = None
    comment: Optional[str] = Field(default=None, max_length=2000)
    actor: Actor


class SynthReviewResponse(BaseModel):
    synth_id: UUID
    final_status: str
    # 이 건이 학습행으로 만들어질 때 쓰일 등급. corrected_grade 를 보냈으면 그 값,
    # 아니면 생성 시 목표 등급. 반려면 학습에 들어가지 않으므로 참고값이다.
    applied_grade: Optional[Grade] = None
    # [2026-09-05] 이 문서가 들어간 학습셋 판 **전부**. 종전에는 단일 값 필드였고 늘 비었다 —
    # 한 문서가 여러 판에 들어갈 수 있으므로 목록이 맞다(tb_sample_dataset_membership).
    dataset_versions: list[str] = Field(default_factory=list)


class SynthJobStatus(BaseModel):
    """생성 작업 1건의 상태 — "이 작업이 성공했나 · 몇 건 만들었나 · 어느 문서인가"."""

    synth_job_id: UUID
    # 잡 저장소가 보고하는 상태. 저장소에 없으면 unknown(만료·재기동 등).
    status: str
    # 실제로 검수큐에 적재된 문서. 생성 건수와 다를 수 있다 — 누출 게이트가 거른다.
    sample_ids: list[UUID] = Field(default_factory=list)
    persisted: Optional[int] = None
    # 누출 게이트 결과(판정·생성수·적재수·사유). 없으면 아직 안 돌았거나 옛 잡이다.
    leakage_gate: Optional[dict] = None
    error: Optional[str] = None

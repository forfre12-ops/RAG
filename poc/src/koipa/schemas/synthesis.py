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
    # [2026-09-10] 본문이 어디서 왔는가. None=LLM 이 정상 응답해 만든 문서.
    # "noop_fallback"=LLM 없이 만든 자리표시 본문. "llm_nonjson"=LLM 이 JSON 을 못 줘서
    # 원문을 그대로 본문으로 쓴 것. 뒤 둘은 학습에서 배제되는데(TRAINING_EXCLUDED_
    # LABEL_SOURCES) **검수자 화면에 그 사실이 없어 본문을 열어야만 알 수 있었다.**
    label_source: Optional[str] = None


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
    # 한 문서가 여러 판에 들어갈 수 있으므로 목록이 맞다(tad_sm_syn_datst_cpst_mng).
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
    # [2026-09-10] LLM 이 실제로 답했는가. {"total","fallback","by_source"} 형태.
    # 종전에는 LLM 이 3회 재시도 끝에 실패해도 잡이 done 이었고 화면·API 어디에도
    # 표시가 없었다 — 로그에만 있었다. status 와 이 칸을 함께 봐야 사실을 알 수 있다.
    llm_fallback: Optional[dict] = None
    error: Optional[str] = None


class SynthCoverageCell(BaseModel):
    """격자 한 칸 — (등급 × 도메인).

    ``real`` 은 그 칸의 **실문서 유래** 건수다. 빈 칸이라도 실문서가 있으면 합성이 급하지
    않다 — 화면이 이 구분을 보여줘야 사람이 무엇을 만들지 고를 수 있다.
    """

    grade: Grade
    domain: str
    n: int
    real: int


class SynthCoverageResponse(BaseModel):
    """합성으로 채울 자리 — 등급 × 도메인 격자.

    화면이 이 값으로 표를 그리고, 칸을 누르면 생성 폼(등급·도메인)이 채워진다. 종전에는
    사람이 터미널에서 표를 읽고 조합을 외운 뒤 폼에 손으로 다시 넣어야 했다.

    ⚠ 이 응답은 **후보**다. 판단은 사람이 한다 — caveat 를 화면에 그대로 띄운다.
    """

    # 학습셋을 못 읽으면 available=False + reason. 격자는 참고 정보라, 이것 때문에
    # 생성 화면 전체가 죽으면 안 된다(500 을 내지 않는다).
    available: bool
    reason: Optional[str] = None
    dataset_dir: Optional[str] = None

    documents: int = 0
    grades: dict[str, int] = Field(default_factory=dict)
    domains: list[str] = Field(default_factory=list)
    cells_total: int = 0
    cells_filled: int = 0
    min_per_cell: int = 0
    # "TS|반도체" -> 건수. 키에 도메인 이름이 들어가므로 한글 키가 그대로 나온다.
    grid: dict[str, int] = Field(default_factory=dict)
    empty: list[SynthCoverageCell] = Field(default_factory=list)
    thin: list[SynthCoverageCell] = Field(default_factory=list)
    # 실문서 유래 비중(0~1). 나머지가 합성이다.
    real_share: float = 0.0
    caveat: Optional[str] = None

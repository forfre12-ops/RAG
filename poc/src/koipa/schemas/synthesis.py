"""Synthesis 도메인 스키마 (OpenAPI /synth/*)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from koipa.modules.m1_synthesis.generator import (
    DOMAIN_ALIASES,
    DOMAIN_DOC_TYPES,
    canonical_domain,
)

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
    # 콘솔은 외부 산업 목록을 문서 주제로 강요하지 않는다. 이 두 값은 조직의 실제
    # 업무 맥락과 문서 유형을 프롬프트에 전달하며, 원문 문서는 받지 않는다.
    scenario_context: str = Field(default="", max_length=1200)
    document_type_hint: str = Field(default="", max_length=300)
    count: int = Field(ge=1, le=500)
    llm_provider: str = Field(default="anthropic", pattern=LLM_PROVIDER_PATTERN)
    actor: Actor

    @model_validator(mode="after")
    def _public_material_requires_s3(self) -> "SynthGenerateRequest":
        """공개 범위를 내부 등급 생성에 섞지 않는다."""
        if canonical_domain(self.domain) == "public" and self.target_grade != Grade.S3:
            raise ValueError("public 도메인은 S3(대외 공개자료) 등급에서만 생성할 수 있습니다")
        return self


class SynthGenerateResponse(BaseModel):
    synth_job_id: UUID
    expected_count: int
    estimated_cost_usd: float
    # 이 응답은 실제 워커 발사가 성공한 경우에만 202으로 반환된다. 준비·발사 실패는
    # 503이며 검토 후보를 만들지 않는다. false 는 저장된 구 응답 호환을 위한 기본값이다.
    dispatched: bool = False
    dispatch_note: str | None = None


class SyntheticDocItem(BaseModel):
    synth_id: UUID
    target_grade: Grade
    domain: Optional[str] = None
    # domain은 생성 주제(산업/업무/공개 범위), document_type은 실제 산출 문서 유형이다.
    document_type: Optional[str] = None
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


class SyntheticDocDetail(SyntheticDocItem):
    """합성 문서 한 건의 전체 본문.

    큐에서는 목록 성능을 위해 발췌문만 보내고, 사람이 ``문서 열기``를 선택했을 때만
    이 계약으로 전체 본문을 가져온다. 합성 원문도 검수 대상이므로 인증된 사용자에게만
    노출하는 synthesis router 의 인증 경계를 그대로 따른다.
    """

    content: str


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


class SynthTrainingCandidateResponse(BaseModel):
    """승인된 모의 문서를 다음 학습에 포함할 수 있는지에 대한 현재 판정."""

    ready: bool
    dataset_version: str
    approved_total: int = 0
    included: int = 0
    excluded_noise: int = 0
    excluded_empty: int = 0
    excluded_gate_error: int = 0
    grade_corrected: int = 0
    blockers: list[str] = Field(default_factory=list)


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


class SynthProviderStatus(BaseModel):
    """provider 하나의 가용성 — 화면이 "고를 수 있는 것"만 보여주는 데 쓴다."""

    name: str
    kind: str  # "deterministic" | "commercial" | "local"
    available: bool
    reason: str = ""


class SynthProvidersStatusResponse(BaseModel):
    """[2026-09-28] 콘솔의 제공자 드롭다운이 "우리가 실제로 가진 것만" 보이게 한다.

    healthz.llm_providers_supported 는 스키마가 받는 값 전부(9개)를 그대로 내려줘서
    "고를 수 있다"와 "지금 이 서버에서 실제로 되는가"가 갈렸다. active 는 서버가 기본으로
    쓰는 provider(설정값)이고, providers 는 advertise 대상(상용 GPT·Claude + 로컬 4종 +
    noop)의 실측 가용성이다.
    """

    active: str
    providers: list[SynthProviderStatus]


class SynthPlaybookResponse(BaseModel):
    """지금 생성에 적용되는 규칙과 조직별 추가 규칙.

    기본 안전 규칙은 코드 정본으로 유지하고, 관리자는 custom_*을 통해 조직의 문서
    관행과 반복 금지 표현을 저장한다.
    """

    enabled: bool
    version: str
    rules: list[str]
    avoid_phrases: list[str]
    text: str
    # 기본 안전 규칙은 읽기 전용, custom_*은 관리자 팝업에서 추가·수정한다.
    baseline_rules: list[str] = Field(default_factory=list)
    baseline_avoid_phrases: list[str] = Field(default_factory=list)
    custom_rules: list[str] = Field(default_factory=list)
    custom_avoid_phrases: list[str] = Field(default_factory=list)
    updated_at: Optional[str] = None
    updated_by: Optional[str] = None


class SynthPlaybookUpdateRequest(BaseModel):
    """조직별 추가 생성 규칙 저장 요청.

    공통 안전 규칙은 삭제할 수 없다. 이 요청은 그 위에 더할 조직별 작성 규칙과 반복
    금지 표현만 교체한다.
    """

    custom_rules: list[str] = Field(default_factory=list, max_length=50)
    custom_avoid_phrases: list[str] = Field(default_factory=list, max_length=100)
    actor: Actor


class SynthCoverageResponse(BaseModel):
    """현재 학습 데이터셋의 구성과 개발용 균형 진단.

    운영 화면은 분할별 합계와 출처를 설명하는 데 사용한다. 등급×외부 산업 격자는
    보조 진단일 뿐, 조직의 생성 주제를 정하거나 생성 폼을 채우는 값이 아니다.
    """

    # 학습셋을 못 읽으면 available=False + reason. 격자는 참고 정보라, 이것 때문에
    # 생성 화면 전체가 죽으면 안 된다(500 을 내지 않는다).
    available: bool
    reason: Optional[str] = None
    dataset_dir: Optional[str] = None

    documents: int = 0
    # 학습 문서 수는 train/val/test 전체 합계다. UI가 숫자의 출처를 설명할 수 있게
    # split별 건수와 실문서/합성 구성을 함께 준다.
    split_counts: dict[str, int] = Field(default_factory=dict)
    source_counts: dict[str, int] = Field(default_factory=dict)
    grades: dict[str, int] = Field(default_factory=dict)
    domains: list[str] = Field(default_factory=list)
    cells_total: int = 0
    cells_filled: int = 0
    min_per_cell: int = 0
    # "TS|반도체" -> 건수. 키에 도메인 이름이 들어가므로 한글 키가 그대로 나온다.
    grid: dict[str, int] = Field(default_factory=dict)
    # 모든 칸의 실문서 유래 건수. thin/empty 목록만으로는 충분한 칸의 구성을 알 수 없다.
    real_grid: dict[str, int] = Field(default_factory=dict)
    empty: list[SynthCoverageCell] = Field(default_factory=list)
    thin: list[SynthCoverageCell] = Field(default_factory=list)
    # 실문서 유래 비중(0~1). 나머지가 합성이다.
    real_share: float = 0.0
    caveat: Optional[str] = None

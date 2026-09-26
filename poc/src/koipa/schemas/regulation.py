"""규정 참고 표시 API 스키마 (설계서 §2.6).

⚠ 점수·유사도·신뢰도 필드를 두지 않는다 — 소비자가 신뢰도로 오용한다(설계서 P3, 콘솔 금지 규칙과 별개로).
⚠ OpenAPI 3.0.3 — ICD(doc/03_openapi_koipa_kl.yaml)에 같은 커밋으로 적는다(시험이 강제한다).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterResponse(BaseModel):
    reg_id: str
    status: str = Field(description="indexing | ready | active | archived | failed")
    duplicate: bool = Field(description="같은 파일이 이미 있으면 true — 새 판을 만들지 않고 기존 reg_id 를 돌려준다")


class RegulationSummary(BaseModel):
    reg_id: str
    name: str
    version_label: str
    status: str
    clause_count: int
    sentence_count: int
    effective_date: str | None = None
    created_at: str | None = None
    activated_at: str | None = None


class RegulationListResponse(BaseModel):
    items: list[RegulationSummary]
    total: int


class RegulationDetail(RegulationSummary):
    filename: str | None = None
    source_format: str | None = None
    split_mode: str | None = Field(default=None, description="article | numbered | paragraph(정확도 낮음)")
    embed_model: str | None = None
    embed_target_count: int = 0
    embedded_count: int = 0
    display_clause_count: int = 0
    scope_note: str | None = None
    scope_confirmed: bool = False
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None


class ClauseItem(BaseModel):
    clause_id: str
    seq: int
    article_no: str
    title: str
    chapter: str
    kind: str = Field(description="general | procedure | grade_def | handling | other")
    kind_source: str = Field(description="auto | admin")
    display: bool
    text: str


class ClauseListResponse(BaseModel):
    items: list[ClauseItem]
    total: int


class ClausePatchRequest(BaseModel):
    display: bool | None = None
    kind: str | None = None


class ActivateRequest(BaseModel):
    scope_confirmed: bool = Field(description="이 규정이 검수 대상 문서의 취급 기준임을 확인했다 — true 여야 활성화된다")
    scope_note: str | None = Field(default=None, max_length=500, description="적용 대상 한 줄 설명")


class EvidenceRegulationRef(BaseModel):
    reg_id: str
    name: str
    version_label: str


class EvidenceClauseRef(BaseModel):
    clause_id: str
    article_no: str
    title: str


class EvidenceItemModel(BaseModel):
    regulation: EvidenceRegulationRef
    clause: EvidenceClauseRef
    sentences: list[str] = Field(description="규정 원문 문장 그대로. 등급별 목록이면 목록 전체(순서대로)")
    is_grade_list: bool


class RegulationEvidenceResponse(BaseModel):
    doc_id: str
    indexed: bool | None = Field(
        default=None, description="문서 대표 벡터가 있는가. null 은 확인하지 않았다는 뜻(활성 규정이 없음)")
    reason: str | None = Field(
        default=None,
        description="items 가 비었을 때 이유 — no_active_regulation | document_not_indexed | embedder_mismatch | "
                    "below_floor | no_sentence | not_applicable | public_document | llm_unavailable | llm_not_local. "
                    "오류가 아니라 정상 응답이다(뒤의 넷은 로컬 LLM 판정 옵션을 켰을 때만 나온다)")
    items: list[EvidenceItemModel]


class PreviewRequest(BaseModel):
    doc_ids: list[str] = Field(min_length=1, max_length=20)


class PreviewResult(BaseModel):
    doc_id: str
    indexed: bool | None = None
    reason: str | None = None
    items: list[EvidenceItemModel]


class PreviewResponse(BaseModel):
    reg_id: str
    results: list[PreviewResult]

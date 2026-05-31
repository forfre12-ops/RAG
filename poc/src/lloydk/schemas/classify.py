from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field, model_validator
from .common import FactorRegistry, Grade


class DocumentInput(BaseModel):
    doc_id: str
    tenant_id: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = Field(default=None, max_length=1_048_576)
    metadata: Optional[dict] = None
    text_already_preprocessed: bool = False


class ClassifyRequest(DocumentInput):
    use_rag: bool = False
    rag_namespace: Optional[str] = None
    return_evidence: bool = True


class EvidenceSpan(BaseModel):
    start: int
    end: int
    text: str
    weight: float = 0.0
    tag: Optional[str] = None


class EvaluationFactors(BaseModel):
    """평가요소 점수.

    기본 4개 named field는 영업비밀 도메인 하위호환용.
    다른 도메인은 scores 딕셔너리에 factor_code → value 형태로 저장.
    named field와 scores는 from_factor_scores()로 동시에 채워짐.
    """

    # 영업비밀 도메인 기본 4요소 (하위호환)
    economic_value: float = 0.0
    non_publicity: float = 0.0
    management_level: float = 0.0
    leak_impact: float = 0.0

    # 도메인 독립 동적 점수 — 모든 factor_code를 담음.
    # 다른 프로젝트에서는 이 필드만 참조하면 충분.
    scores: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def from_factor_scores(cls, factor_scores: dict[str, float]) -> "EvaluationFactors":
        """rule engine의 factor_scores (factor_code → value) → EvaluationFactors.

        FactorRegistry에서 현재 활성 factor map을 로드해:
        - 기본 4요소는 named field에도 채움 (하위호환)
        - 모든 factor는 scores dict에 저장 (도메인 독립)
        """
        field_map = FactorRegistry.get_field_map()
        named: dict[str, float] = {}
        for code, value in factor_scores.items():
            field_name = field_map.get(code)
            # named field로 매핑 가능한 기본 4요소만 named에 설정
            if field_name in {"economic_value", "non_publicity", "management_level", "leak_impact"}:
                named[field_name] = value
        return cls(**named, scores=factor_scores)

    @model_validator(mode="after")
    def _sync_scores(self) -> "EvaluationFactors":
        """named field → scores 동기화 (직접 생성 시 scores가 비어있을 경우 보완)."""
        if not self.scores:
            field_map = FactorRegistry.get_field_map()
            inv = {v: k for k, v in field_map.items()}
            self.scores = {
                inv.get(f, f.upper()): getattr(self, f)
                for f in ("economic_value", "non_publicity", "management_level", "leak_impact")
                if getattr(self, f, 0.0) != 0.0
            }
        return self


class RagContextHit(BaseModel):
    source_doc: str
    chunk_id: str
    score: float


class ClassifyResponse(BaseModel):
    inference_id: UUID
    doc_id: str
    label: Grade
    confidence: float
    scores: dict[str, float]
    evaluation_factors: Optional[EvaluationFactors] = None
    evidence: list[EvidenceSpan] = []
    rag_context_used: list[RagContextHit] = []
    model_version: str
    elapsed_ms: int
    status: str = "staging"
    warnings: list[str] = []

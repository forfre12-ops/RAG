from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field
from .common import Grade


class DocumentInput(BaseModel):
    doc_id: str
    tenant_id: Optional[str] = None
    title: Optional[str] = None
    content: str = Field(max_length=1_048_576)
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
    economic_value: float = 0.0
    non_publicity: float = 0.0
    management_level: float = 0.0
    leak_impact: float = 0.0


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

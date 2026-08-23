from typing import Optional
from uuid import UUID
from pydantic import BaseModel, Field, model_validator
from .common import FactorRegistry, Grade


class DocumentInput(BaseModel):
    doc_id: str
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

    # 정본 가이드 3요건 (B안 S×V×M)
    secrecy: float = 0.0          # 비공지성(S)
    value: float = 0.0            # 경제적 유용성(V)
    management: float = 0.0       # 비밀관리성(M)

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
            # DB field_map이 구(舊) 4요소로 stale일 수 있어, 정본 코드는 code.lower()로 직접 매핑
            field_name = field_map.get(code) or code.lower()
            # named field로 매핑 가능한 정본 3요건만 named에 설정
            if field_name in {"secrecy", "value", "management"}:
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
                for f in ("secrecy", "value", "management")
                if getattr(self, f, 0.0) != 0.0
            }
        return self


class RagContextHit(BaseModel):
    source_doc: str
    chunk_id: str
    score: float
    text: str = ""  # 검색된 청크 본문 — 답변 합성 프롬프트에 실제 근거로 투입


class AutomationAssessment(BaseModel):
    """자동확정 정책을 검증하기 위해 동결하는 비민감 판단 근거.

    이 객체는 그림자 모드 관측치다. ``selected_confidence``를 임의로 변환한 새
    confidence나 즉시 적용되는 자동확정 결정은 포함하지 않는다.
    """

    schema_version: str
    shadow_mode: str = "collect_only"
    selected_label: str
    selected_confidence: float
    selected_rank: Optional[int] = None
    top_label: Optional[str] = None
    top_score: Optional[float] = None
    runner_up_label: Optional[str] = None
    runner_up_score: Optional[float] = None
    score_margin: Optional[float] = None
    rule_grade: Optional[str] = None
    model_grade: Optional[str] = None
    rule_agrees: Optional[bool] = None
    rule_has_evidence: Optional[bool] = None
    evidence_count: int = 0
    rag_context_count: int = 0
    current_policy_status: str
    current_policy_eligible: bool
    causal_review_reason: Optional[str] = None
    review_gate_hits: list[str] = Field(default_factory=list)


class ClassifyResponse(BaseModel):
    inference_id: UUID
    doc_id: str
    label: Grade
    confidence: float
    scores: dict[str, float]
    evaluation_factors: Optional[EvaluationFactors] = None
    # [번들 C] evaluation_factors(S/V/M)의 출처 — 법리 근거 오인 방지(컴플라이언스).
    #   "rule_evidenced": 룰엔진이 실제 본문 증거로 산출한 factor(법리 근거로 표시 가능).
    #   "model_estimated": 모델/청크집계 등급에 맞춰 역산(svm_levels_for_grade)한 추정치 —
    #     룰이 미탐했을 때 '등급↔factor 모순 표기'를 막으려 정합화한 값이라 법리 근거 아님.
    # UI/리포트는 model_estimated를 '모델 추정'으로 구분 표시할 것.
    factors_source: str = "rule_evidenced"
    # [2026-08-20] factors_source == "model_estimated" 일 때 **룰이 실제로 관측한**
    #   S/V/M. 종전에는 역산값이 원본을 덮어써서, 화면에 "S2·V2·M2 인데 룰은 S1" 처럼
    #   판정식(grade_from_svm)으로 설명되지 않는 조합이 떴다(사용자 지적 2026-08-20).
    #   두 벌을 나란히 보여 주면 왜 룰과 모델이 갈렸는지가 그 자리에서 읽힌다.
    #   역산이 없었으면 None — 그때는 evaluation_factors 가 곧 룰 관측값이다.
    rule_evaluation_factors: Optional[EvaluationFactors] = None
    evidence: list[EvidenceSpan] = []
    rag_context_used: list[RagContextHit] = []
    model_version: str
    elapsed_ms: int
    status: str = "staging"
    warnings: list[str] = []
    # [투명성/시연] 하이브리드 서빙의 각 엔진 원시 판정 — 룰·모델·최종을 대조 표시.
    #   rule_grade: 룰 엔진(시드 키워드 S×V×M) 단독 판정.
    #   model_grade: 학습 분류기(BERT) 단독 판정(override/cap/floor 이전). 모델 미로드 시 None.
    #   decision_path: label(최종)이 어떻게 나왔는지 — agreement/rule-override/source-cap/rule-only 등.
    rule_grade: Optional[str] = None
    model_grade: Optional[str] = None
    decision_path: Optional[str] = None
    # 자동확정 위험도 보정 전의 그림자 관측치. 정책을 바꾸지 않고 검수 결과와 연결한다.
    automation_assessment: Optional[AutomationAssessment] = None

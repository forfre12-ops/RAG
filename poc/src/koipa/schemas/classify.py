from typing import Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field
from .common import FactorRegistry, Grade
from .regulation import EvidenceItemModel


class DocumentInput(BaseModel):
    doc_id: str
    content: Optional[str] = Field(default=None, max_length=1_048_576)
    metadata: Optional[dict] = None
    text_already_preprocessed: bool = False


class ClassifyRequest(DocumentInput):
    return_evidence: bool = True


class EvidenceSpan(BaseModel):
    start: int
    end: int
    text: str
    weight: float = 0.0
    tag: Optional[str] = None


class FactorDetail(BaseModel):
    """평가요소 한 축(S/V/M 중 하나)의 판정 — 역산 금지, 근거 없으면 unknown.

    [2026-10-02] 종전에는 본문 증거가 없어도 등급에서 거꾸로 계산한 값(svm_levels_for_grade)을
    그대로 내려보냈다 — 감사 관점에서 "이 수치가 진짜 근거냐"에 답할 수 없는 상태였다(사용자
    지적). 이제 근거가 실제로 관측됐을 때만 value·evidence를 채우고, 없으면 state="unknown"·
    value=None으로 정직하게 비워둔다. unknown은 0(공개/무가치/무관리 확인됨)과 다르다 —
    "모른다"와 "아니라고 확인됐다"를 구분한다.
    """

    state: Literal["observed", "unknown"]
    value: Optional[int] = None   # state="observed"일 때만 0/1/2
    evidence: list[str] = Field(default_factory=list)  # state="observed"일 때만 채움


class EvaluationFactors(BaseModel):
    """평가요소 판정 — 정본 가이드 3요건(S×V×M), 축마다 독립 관측.

    기본 4개 named field는 영업비밀 도메인 하위호환용.
    다른 도메인은 scores 딕셔너리에 factor_code → value 형태로 저장.
    """

    # 정본 가이드 3요건 (B안 S×V×M) — 각 축은 독립 관측(FactorDetail), 등급에서 역산 안 함.
    secrecy: FactorDetail     # 비공지성(S)
    value: FactorDetail       # 경제적 유용성(V)
    management: FactorDetail  # 비밀관리성(M)

    # 도메인 독립 동적 점수 — 관측된 축만 담음(값 없는 축은 생략). 외부 소비처 미확인(2026-10-02).
    scores: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def from_axis_results(
        cls,
        *,
        secrecy: tuple[bool, Optional[int], list[str]],
        value: tuple[bool, Optional[int], list[str]],
        management: tuple[bool, Optional[int], list[str]],
    ) -> "EvaluationFactors":
        """(evidenced, level, evidence_texts) 3축 → EvaluationFactors.

        evidenced=False면 level·evidence_texts는 무시되고 state="unknown"·value=None이 된다
        (호출부가 실수로 숫자를 같이 넘겨도 역산 재유입을 막는다).
        """
        def _detail(evidenced: bool, lv: Optional[int], ev: list[str]) -> FactorDetail:
            if not evidenced:
                return FactorDetail(state="unknown")
            return FactorDetail(state="observed", value=lv, evidence=ev)

        s, v, m = _detail(*secrecy), _detail(*value), _detail(*management)
        field_map = FactorRegistry.get_field_map()
        inv = {fname: code for code, fname in field_map.items()}
        scores = {
            inv.get(name, name.upper()): float(d.value)
            for name, d in (("secrecy", s), ("value", v), ("management", m))
            if d.value is not None
        }
        return cls(secrecy=s, value=v, management=m, scores=scores)


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
    current_policy_status: str
    current_policy_eligible: bool
    causal_review_reason: Optional[str] = None
    review_gate_hits: list[str] = Field(default_factory=list)


class ClassifyOutcome(BaseModel):
    """분류 결과의 공통 항목 — 동기 응답(ClassifyResponse)과 비동기 작업 결과(ClassifyJobResult)가 같이 쓴다.

    필수는 inference_id · doc_id · label · confidence · scores · model_version 여섯이다. 나머지는 분류를 끝까지 수행했을 때만 채워진다 —
    두 경우에는 evaluation_factors · rule_evaluation_factors · evidence · rule_grade · model_grade · decision_path · grade_candidates ·
    grade_candidates_reason 이 비고(None 또는 빈 목록) factors_source 는 의미 없는 기본값이다
    (services/classify_service.py 의 조기 반환 두 곳):
      ① 본문을 읽을 수 없다(등록된 doc_id 를 못 찾고 content 도 없다) — 최고 등급·confidence 0·status=needs_review·model_version="none" 으로 검수에 격리한다.
      ② 사람이 이미 확정한 등급이 있다 — 추론을 건너뛰고 그 등급을 돌려준다(model_version="human_review:…"·status=staging).
    두 경우는 model_version 과 warnings 로 가른다.

    [2026-09-27] automation_assessment 는 여기 없다 — KL 이 부르는 IF-05(GET /classify/jobs/{job_id})·콜백에
    실리지 않게 ClassifyResponse 로만 옮겼다('불필요한 건 API 에서 최대한 빼자'는 원칙에 따라). 그 값 자체는
    _try_persist 가 DB(tad_cm_clsf_rslt_mng.automation_assessment)에 그대로 남기므로 우리 내부 집계
    (scripts/analyze_auto_confirm_shadow.py --from-db)는 이 변경과 무관하다.
    """

    inference_id: UUID
    doc_id: str
    label: Grade
    confidence: float
    scores: dict[str, float]
    evaluation_factors: Optional[EvaluationFactors] = None
    # [번들 C, 2026-10-02 재정의] evaluation_factors(S/V/M) 중 근거 없는(state="unknown") 축이
    #   있는지 요약 — 법리 근거 오인 방지(컴플라이언스). 축 3개 전부 state="observed"면
    #   "rule_evidenced", 하나라도 "unknown"이면 "model_estimated". 각 축이 FactorDetail.state를
    #   직접 들고 있으므로(2026-10-02 이전엔 문서 단위 플래그 하나뿐이었다) 세세한 판단은
    #   evaluation_factors.<축>.state를 직접 볼 것 — 이 필드는 요약용 파생값이다.
    factors_source: str = "rule_evidenced"
    # [2026-10-02] 폐기 예정 — 종전엔 등급에 맞춰 역산한 값이 evaluation_factors를 덮어쓸 때
    #   덮어쓰기 전 원래 관측값을 보존하는 용도였다. 이제 역산 자체를 안 하므로(근거 없으면
    #   evaluation_factors가 바로 state="unknown") 항상 None — evaluation_factors 자체가 곧
    #   룰 관측값이다. 하위 호환을 위해 필드만 남겨둔다.
    rule_evaluation_factors: Optional[EvaluationFactors] = None
    evidence: list[EvidenceSpan] = []
    model_version: str
    status: str = "staging"
    warnings: list[str] = []
    # [투명성/시연] 하이브리드 서빙의 각 엔진 원시 판정 — 룰·모델·최종을 대조 표시.
    #   rule_grade: 룰 엔진(시드 키워드 S×V×M) 단독 판정.
    #   model_grade: 학습 분류기(BERT) 단독 판정(override/cap/floor 이전). 모델 미로드 시 None.
    #   decision_path: label(최종)이 어떻게 나왔는지 — agreement/rule-override/source-cap/rule-only 등.
    rule_grade: Optional[str] = None
    model_grade: Optional[str] = None
    decision_path: Optional[str] = None
    # [후보집합] 비밀관리성(M)을 못 받아 **아직 하나로 정해지지 않은** 등급들. 비어 있으면
    # 갈릴 것이 없다는 뜻이다(label 이 유일한 답).
    #
    # label 을 대체하지 않는다 — 기존 계약을 지키려고 예측값은 그대로 둔다. 다만 정본
    # 공식에서 S1 은 (2,2,0) 하나뿐이라 S1 과 TS 를 가르는 것은 **오직 M** 인데 그 공급이
    # 0 건이다(전 데이터셋 432,820행). 그 상태에서 단일 등급만 내보내면 없는 정보를 있는
    # 척하는 것이 된다. 검수자가 확인해야 할 것이 등급이 아니라 **접근권한**임을 알린다.
    grade_candidates: list[str] = []
    grade_candidates_reason: Optional[str] = None
    # [2026-09-29] RAG+LLM 규정참고(설계서 §2.5·§3.6) — 이 문서에 해당하는 규정 원문이 있을
    # 때만 채운다. 등급 판정과 무관한 참고용이며, GPU 없는 배포(현재 고객사 운영 서버)에서는
    # 로컬 LLM 옵션이 시간 안에 못 끝나 항상 None 이 된다 — 필드는 지금 만들어 두고 GPU 도입은
    # 별도 과제([[regulation-reference-implemented-2026-09-25]]).
    regulation_reference: Optional[list[EvidenceItemModel]] = None


def kl_wire_projection(
    result: "ClassifyOutcome", regulation_reference: Optional[list[EvidenceItemModel]] = None
) -> "ClassifyOutcome":
    """KL(지재원 포털)이 실제로 받을 사본 — 같은 클래스, 진단용 필드만 비운다.

    [2026-09-29] KL 요청: "등급이 같으면 예상 등급 하나, 다르면 분류기(label)를 메인으로 하고
    룰분류기 예측은 따로, RAG+LLM 인 경우엔 관련 참고도 같이". label(최종판정)은 이미 시스템의
    대표 답이므로 그대로 메인으로 두고, rule_grade 는 label 과 같으면 지워 "하나만" 리턴되게 한다.
    evaluation_factors·evidence·decision_path 등 룰/모델 결합 근거를 보여주는 내부 진단 필드는
    KL 요청 범위 밖이라 비운다 — DB 저장·우리 콘솔(admin.html)·`GET /classify/jobs/{job_id}` 를
    관리자/시스템 역할로 부르는 내부 대용량 테스트 화면은 이 함수를 거치지 않아 그대로 전체를 본다
    (api/async_classify.py 의 kl_backend 역할 분기, workers/tasks.py 의 콜백 발사 지점).
    """
    rule_grade = result.rule_grade if result.rule_grade is not None and result.rule_grade != result.label else None
    return result.model_copy(update={
        "scores": {},
        "evaluation_factors": None,
        "rule_evaluation_factors": None,
        "evidence": [],
        "model_grade": None,
        "decision_path": None,
        "grade_candidates": [],
        "grade_candidates_reason": None,
        "rule_grade": rule_grade,
        "regulation_reference": regulation_reference,
    })


class ClassifyResponse(ClassifyOutcome):
    """POST /classify · /classify/stream · /classify/explain 의 응답 — 요청을 처리한 시간을 잰다.

    이 셋은 전부 x-audience: internal(우리 콘솔·리뷰 화면 전용, KL 은 호출하지 않는다)이라
    automation_assessment(그림자 자동확정 관측치)를 여기서만 싣는다.
    """

    elapsed_ms: int
    # 자동확정 위험도 보정 전의 그림자 관측치. 정책을 바꾸지 않고 검수 결과와 연결한다.
    # [2026-09-27] ClassifyOutcome 이 아니라 여기(내부 전용 응답)에만 둔다 — 규약서(03_openapi_koipa_kl.yaml)
    # 가 스스로 "연동에 쓰지 않으며 예고 없이 바뀔 수 있다"고 적어 둔 값을 KL 이 받는 job_result()/ClassifyJobResult
    # 에는 안 싣기 위해서다. DB 저장(_try_persist)은 이 필드를 그대로 받아 독립적으로
    # 남기므로 scripts/analyze_auto_confirm_shadow.py 같은 내부 집계는 영향 없다.
    automation_assessment: Optional[AutomationAssessment] = None

    def job_result(self) -> dict:
        """비동기 작업 결과(IF-05 results[] · 콜백 본문)로 저장할 JSON — KL 이 실제로 받는 값.

        elapsed_ms 는 비동기 결과는 시간을 재지 않으므로 안 싣는다. automation_assessment 는 내부 전용
        그림자 관측치라 안 싣는다(2026-09-27) — 값은 DB 에 별도로 남는다(위 클래스 docstring 참고).
        """
        return self.model_dump(mode="json", exclude={"elapsed_ms", "automation_assessment"})


class ClassifyJobResult(ClassifyOutcome):
    """GET /classify/jobs/{job_id} 의 results[] 한 건(IF-05, KL 이 호출) — ClassifyOutcome 그대로이며 경과 시간·
    automation_assessment 는 없다(둘 다 ClassifyResponse 에만 있는 내부 전용 항목)."""


class StoredClassificationResponse(BaseModel):
    """GET /classify/{doc_id} — DB 에 저장된 최근 분류 결과(진실 소스)와 사람이 확정한 등급.

    저장값만으로 응답을 만들므로 근거(evidence)·평가요소·룰/모델 판정·경고는 여기에 없다. 그것들은 분류를
    실행한 응답(POST /classify)과 비동기 작업 결과(GET /classify/jobs/{job_id})에만 있다. 예전에는
    ClassifyResponse 를 그대로 써서 그 항목들이 항상 빈 채로 나갔다.
    """

    inference_id: UUID
    doc_id: str
    label: Grade
    confidence: float
    scores: dict[str, float]
    model_version: str
    status: str = "staging"

    # [KL 연동 2026-08-26] 사람이 확정한 등급. 예측(label)과 별개다.
    #
    # 왜. confirm 은 예측 등급을 덮어쓰지 않는다 — 모델이 뭐라 했는지와 사람이 뭘로 정했는지를
    # 둘 다 남기는 설계이고, 사람 판단은 tad_cm_crct_mng 에 적힌다. 그래서 확정 뒤에도
    # GET /classify/{doc_id} 의 label 은 예측 등급 그대로다(실측 2026-08-26: 예측 S2 를
    # S1 으로 확정했는데 조회는 S2). KL 이 확정 등급을 받으려면 승격 대기 목록을 우회
    # 조회해야 했다 — 이름도 의미도 맞지 않고 승격되면 목록에서 사라진다.
    #
    # 아래 세 필드는 교정 기록에서 읽어 채운다. 교정이 없으면 None 이라 기존 응답과 같다
    # (추가 전용 · 하위호환). label 은 예측으로 남겨 감사 증적을 보존한다.
    confirmed_label: Optional[str] = None
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None

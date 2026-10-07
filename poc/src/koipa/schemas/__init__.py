from .common import Grade, GradeDefinition, Actor, Error
from .classify import (
    DocumentInput,
    ClassifyRequest,
    ClassifyResponse,
    ClassifyJobResult,
    StoredClassificationResponse,
    EvidenceSpan,
    EvaluationFactors,
    FactorDetail,
)
from .classify_async import (
    ClassifyAsyncRequest,
    ClassifyAsyncResponse,
    ClassifyBatchRequest,
    ClassifyBatchResponse,
    ClassifyJobStatus,
)
from .confirm import (
    ConfirmRequest,
    ConfirmResponse,
    RelabelRequest,
    RelabelResponse,
)
from .training import (
    TrainRequest,
    TrainResponse,
    TrainStatus,
    TrainJobSummary,
    TrainJobList,
)
# [2026-10-07 KL 분리 조사] 고객사 전용 이미지(Dockerfile.api.customer·Dockerfile.worker.customer)
# 에는 schemas/synthesis.py 가 물리적으로 없다. 이 패키지의 __init__.py 는 `import koipa.schemas.*`
# 아무거나 하나만 해도 무조건 실행되므로(파이썬 패키지 임포트 규칙), 여기서 못 잡으면 schemas
# 패키지를 쓰는 전부(koipa.schemas.common.Error 포함)가 ModuleNotFoundError 로 깨진다 — 실제로
# 빌드한 이미지를 부팅해서 발견했다(grep 만으로는 __init__.py 의 패키지 단위 임포트 규칙을 놓친다).
# 아래 이름을 바깥에서 쓰는 곳은 synthesis 생태계(이미 함께 빠지는 코드)뿐이라 없어도 안전하다.
try:
    from .synthesis import (
        SynthGenerateRequest,
        SynthGenerateResponse,
        SyntheticDocItem,
        SynthQueueResponse,
        SynthReviewRequest,
        SynthReviewResponse,
    )
except ModuleNotFoundError:
    pass
from .schema_admin import (
    GradesGetResponse,
    GradesPutRequest,
    GradesPutResponse,
)
from .metrics import (
    MetricsReport,
    MetricsHistory,
    ConfusionMatrix,
)

__all__ = [
    "Grade", "GradeDefinition", "Actor", "Error",
    "DocumentInput", "ClassifyRequest", "ClassifyResponse", "ClassifyJobResult", "StoredClassificationResponse",
    "EvidenceSpan", "EvaluationFactors", "FactorDetail",
    "ClassifyAsyncRequest", "ClassifyAsyncResponse",
    "ClassifyBatchRequest", "ClassifyBatchResponse", "ClassifyJobStatus",
    "ConfirmRequest", "ConfirmResponse", "RelabelRequest", "RelabelResponse",
    "TrainRequest", "TrainResponse", "TrainStatus", "TrainJobSummary", "TrainJobList",
    "GradesGetResponse", "GradesPutRequest", "GradesPutResponse",
    "MetricsReport", "MetricsHistory", "ConfusionMatrix",
]
# synthesis 스키마는 고객사 전용 이미지에 없을 수 있어 실제로 임포트됐을 때만 __all__ 에 넣는다
# (위 try/except 참고) — `from koipa.schemas import *` 가 없는 이름을 끌어오려다 깨지지 않게.
if "SynthGenerateRequest" in globals():
    __all__ += [
        "SynthGenerateRequest", "SynthGenerateResponse", "SyntheticDocItem",
        "SynthQueueResponse", "SynthReviewRequest", "SynthReviewResponse",
    ]

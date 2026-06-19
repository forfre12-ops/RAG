"""M6 평가·능동학습 (FUN-024).

운영 중 모델의 성능을 평가하고, 관리자 보정(corrections)을 누적해
재학습 큐를 트리거. doc/04 §6, doc/01 §2.3 모듈 6.

핵심 KPI:
- F1-macro, Accuracy, Precision, Recall
- **FNR per-grade** (보안 미탐 = 핵심 위험)
- Confusion Matrix + 고등급→저등급 미탐 알람
- Active Learning: corrections 누적 → 재학습 트리거 판단
"""

from lloydk.modules.m6_evaluation.metrics import (
    MetricsResult,
    compute_metrics_from_arrays,
    compute_metrics_from_db,
)
from lloydk.modules.m6_evaluation.confusion_matrix import (
    ConfusionMatrixResult,
    build_confusion_matrix,
    detect_underclass_alarms,
)
from lloydk.modules.m6_evaluation.active_learning import (
    ActiveLearningStatus,
    evaluate_retraining_need,
)
from lloydk.modules.m6_evaluation.deploy_gate import (
    DeployDecision,
    GateCheck,
    evaluate_deploy_gate,
)
from lloydk.modules.m6_evaluation.serving_eval import evaluate_via_serving
from lloydk.modules.m6_evaluation.report import (
    render_html_report,
    render_confusion_matrix_png,
)
from lloydk.modules.m6_evaluation.retrieval_metrics import (
    RetrievalMetricsResult,
    compute_retrieval_metrics_from_arrays,
    compute_retrieval_metrics_from_db,
)

__all__ = [
    "MetricsResult",
    "compute_metrics_from_arrays",
    "compute_metrics_from_db",
    "ConfusionMatrixResult",
    "build_confusion_matrix",
    "detect_underclass_alarms",
    "ActiveLearningStatus",
    "evaluate_retraining_need",
    "DeployDecision",
    "GateCheck",
    "evaluate_deploy_gate",
    "evaluate_via_serving",
    "render_html_report",
    "render_confusion_matrix_png",
    "RetrievalMetricsResult",
    "compute_retrieval_metrics_from_arrays",
    "compute_retrieval_metrics_from_db",
]

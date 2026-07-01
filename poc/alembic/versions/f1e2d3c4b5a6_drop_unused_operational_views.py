"""미사용 운영 뷰 3개 DROP — 보정 로직 단일 진실원화.

Revision ID: f1e2d3c4b5a6
Revises: b1c2d3e4f5a6
Create Date: 2026-06-15

배경:
- baseline(000000000001)에 운영 뷰 4개가 정의됐으나, 그 중 3개
  (v_classification_final / v_model_performance / v_active_learning_status)는
  애플리케이션 코드에서 전혀 쿼리되지 않는 dead asset이다. 동일 로직이
  metrics.py / active_learning.py / confirm_service.py에 코드로 재구현돼 있다.
- 더 문제: 이 뷰들은 보정(correction) 해석에 `direction != 'confirm'`을 쓰는데,
  metrics.py는 [M-metrics-confirm]에서 "direction 무관, 최신 correction의
  corrected_level_id를 정답으로" 쓰도록 바뀌었다. confirm 시 corrected_level_id가
  predicted_level_id로 저장되므로(confirm_service.py) **현재는** 두 로직의 결과가
  같지만, confirm 저장 방식이 미래에 바뀌면 뷰와 코드가 갈라진다. 영업비밀 도메인에서
  미탐(under-class) 집계가 두 진실원에서 다르게 나오면 위험하다.
- 따라서 보정 로직을 가진 뷰 3개를 제거해 코드를 단일 진실원으로 통일한다.
  순수 비용 집계 뷰 v_monthly_llm_cost는 보정 로직과 무관(함정 없음)하므로 유지한다.

가역성:
- downgrade는 baseline의 뷰 정의를 그대로 복원한다(운영 롤백 안전).
"""
from typing import Sequence, Union

from alembic import op


revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DROP = """
DROP VIEW IF EXISTS v_active_learning_status;
DROP VIEW IF EXISTS v_model_performance;
DROP VIEW IF EXISTS v_classification_final;
"""

# baseline(000000000001) 정의를 1:1 복원 — downgrade 안전망.
_RESTORE = r"""
CREATE OR REPLACE VIEW v_classification_final AS
SELECT
  c.classification_id,
  c.doc_id,
  c.tenant_id,
  c.model_version,
  c.predicted_level_id,
  pl.level_code AS predicted_code,
  COALESCE(latest_corr.corrected_level_id, c.predicted_level_id) AS final_level_id,
  COALESCE(fl.level_code, pl.level_code) AS final_code,
  c.confidence,
  c.status,
  c.classified_at,
  latest_corr.corrected_at
FROM tb_classifications c
JOIN tb_classification_levels pl ON c.predicted_level_id = pl.level_id
LEFT JOIN LATERAL (
  SELECT corrected_level_id, corrected_at
  FROM tb_corrections
  WHERE classification_id = c.classification_id AND direction != 'confirm'
  ORDER BY corrected_at DESC LIMIT 1
) latest_corr ON true
LEFT JOIN tb_classification_levels fl ON latest_corr.corrected_level_id = fl.level_id;

CREATE OR REPLACE VIEW v_model_performance AS
SELECT c.model_version,
       cl.level_code, cl.level_name,
       COUNT(*) AS total_classified,
       COUNT(*) FILTER (WHERE c.status = 'confirmed') AS confirmed_count,
       COUNT(*) FILTER (WHERE c.status = 'corrected') AS corrected_count,
       COUNT(*) FILTER (WHERE cr.direction = 'underclass') AS underclass_count,
       COUNT(*) FILTER (WHERE cr.direction = 'overclass') AS overclass_count,
       AVG(c.confidence)::DECIMAL(5,4) AS avg_confidence,
       AVG(c.inference_ms)::INT AS avg_inference_ms
FROM tb_classifications c
JOIN tb_classification_levels cl ON c.predicted_level_id = cl.level_id
LEFT JOIN tb_corrections cr ON c.classification_id = cr.classification_id
GROUP BY c.model_version, cl.level_code, cl.level_name;

CREATE OR REPLACE VIEW v_active_learning_status AS
SELECT
  COUNT(*) FILTER (WHERE consumed_in_run IS NULL) AS unconsumed_corrections,
  COUNT(*) FILTER (WHERE direction = 'underclass' AND consumed_in_run IS NULL) AS pending_underclass,
  MAX(corrected_at) AS last_correction_at,
  CASE
    WHEN COUNT(*) FILTER (WHERE direction = 'underclass'
                          AND consumed_in_run IS NULL) >= 10 THEN 'URGENT_RETRAIN'
    WHEN COUNT(*) FILTER (WHERE consumed_in_run IS NULL) >= 50 THEN 'RETRAIN_RECOMMENDED'
    ELSE 'OK'
  END AS retrain_status
FROM tb_corrections;
"""


def upgrade() -> None:
    """미사용 운영 뷰 3개 DROP (idempotent — IF EXISTS)."""
    op.execute(_DROP)


def downgrade() -> None:
    """baseline 뷰 정의 복원."""
    op.execute(_RESTORE)

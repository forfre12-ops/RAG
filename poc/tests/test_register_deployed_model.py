"""scripts/register_deployed_model.py — GA 배포모델 등록(활성화 X) 단위·통합 검증."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import register_deployed_model as rdm  # noqa: E402
from register_deployed_model import metrics_from_report  # noqa: E402

_REPORT = {
    "model_version": "v-test",
    "accuracy": 0.7831,
    "precision_macro": 0.7909,
    "recall_macro": 0.7833,
    "f1_macro": 0.7833,
    "fnr_overall": 0.2169,
    "fnr_by_grade": {"TS": 0.3179, "S1": 0.1792, "S2": 0.1850, "S3": 0.1845},
    "confusion_matrix": [[118, 11, 15, 29], [9, 142, 9, 13], [5, 3, 141, 24], [8, 13, 10, 137]],
}


def _pg_ok() -> bool:
    try:
        from lloydk.db import session_scope
        from sqlalchemy import text
        with session_scope() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# ── 순수 매핑(런타임 report 실측 — 하드코딩 아님) ─────────────────────────

def test_metrics_from_report_maps_fields_and_counts_samples():
    m = metrics_from_report(_REPORT, Path("artifacts/x/report.json"))
    assert m["f1_macro"] == 0.7833
    assert m["fnr_overall"] == 0.2169
    assert m["fnr_by_grade"]["TS"] == 0.3179
    # sample_count 는 confusion_matrix 셀 합(173+173+173+168=687)
    assert m["sample_count"] == 687
    assert m["eval_source"].endswith("report.json")


def test_metrics_from_report_sample_count_falls_back_without_cm():
    rep = {k: v for k, v in _REPORT.items() if k != "confusion_matrix"}
    rep["sample_count"] = 500
    m = metrics_from_report(rep, Path("r.json"))
    assert m["sample_count"] == 500


# ── DB 통합(멱등 + is_active=False = 게이트 우회 안 함) ─────────────────────

@pytest.mark.fullstack
def test_register_is_idempotent_and_inactive(tmp_path, monkeypatch):
    if not _pg_ok():
        pytest.skip("Postgres not reachable")
    from sqlalchemy import func, select

    from lloydk.db import session_scope
    from lloydk.db.models import ModelVersion
    from lloydk.repositories.training_repo import TrainingRepo

    (tmp_path / "report.json").write_text(json.dumps(_REPORT), encoding="utf-8")
    label = "test-reg-" + uuid.uuid4().hex[:8]
    monkeypatch.setattr(sys, "argv", ["register_deployed_model.py", "--model-dir", str(tmp_path), "--label", label])

    try:
        assert rdm.main() == 0          # 신규 등록
        assert rdm.main() == 0          # 재실행 = 멱등(update, 중복 없음)
        with session_scope() as db:
            repo = TrainingRepo(db)
            mv = repo.get_by_label(label)
            assert mv is not None
            assert mv.is_active is False              # 등록만 — 활성은 POST /admin/model/activate 게이트로만
            assert mv.training_data_count == 687
            assert mv.metrics["f1_macro"] == 0.7833   # report.json 실측 적재
            cnt = db.execute(
                select(func.count()).select_from(ModelVersion).where(ModelVersion.version_label == label)
            ).scalar()
            assert cnt == 1                           # 멱등 — 중복 행 없음
    finally:
        with session_scope() as db:
            mv = TrainingRepo(db).get_by_label(label)
            if mv is not None:
                db.delete(mv)

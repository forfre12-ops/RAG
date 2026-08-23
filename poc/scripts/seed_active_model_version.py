"""데모 사전 세팅 — 배포 분류기를 활성 ModelVersion으로 DB에 등록·활성화.

admin 콘솔(/demo/admin.html)의 '핫 리로드 → 메트릭' 폐곡선은 DB에 is_active=True인
ModelVersion 행이 있어야 /api/v1/metrics/latest 가 200을 준다. 프레시 데모 DB엔 이 행이
없어서(모델은 .env CLASSIFIER_MODEL_DIR 폴백으로 로드) metrics/latest 가 404 "no active
model"을 내고, 리로드 직후 loadMetrics() 가 그 404를 표시한다. 거버넌스 스케줄러/CLI가
수행하던 등록·활성화 단계를 이 스크립트가 대신한다 (멱등).

메트릭은 v5_clean 배포본 v-fe4b386b 의 실측 평가(report.json). 직전 배포본=v4_clean/v-dd3abab9.

실행 (poc 디렉터리에서, .env 로드를 위해):
    .venv\\Scripts\\python.exe scripts\\seed_active_model_version.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.db import session_scope  # noqa: E402
from koipa.repositories.training_repo import TrainingRepo  # noqa: E402

LABEL = "v-fe4b386b"
BASE_MODEL = "kakaobank/kf-deberta-base"
MODEL_URI = "artifacts/classifier_p1_v5_clean/v-fe4b386b"

# 실측 — v5_clean 배포본(v-fe4b386b) report.json (2026-07-29 gate_p1_candidate 3축 PASS 승격).
# 직전 배포본=v4_clean/v-dd3abab9(accuracy 0.7831·fnr 0.2169) — 롤백 타깃.
# macro precision/recall 은 per_class 평균, fnr_by_grade 는 confusion_matrix 기반 하향오분류율.
# ⚠️ 하드코딩 시드값(런타임에 report.json 을 읽지 않음). 모델 재학습/교체 시 eval_source 의
#    report.json 값과 함께 수동 갱신해야 한다(드리프트 주의). 데모 metrics/latest 시딩 전용.
METRICS = {
    "accuracy": 0.9531,
    "precision_macro": 0.9566,
    "recall_macro": 0.9474,
    "f1_macro": 0.9515,
    "fnr_overall": 0.0469,            # = fnr_underclass (하향 오분류 = 무인 미탐 위험)
    "fnr_by_grade": {"TS": 0.1000, "S1": 0.0217, "S2": 0.0690, "S3": 0.0196},
    "sample_count": 256,              # test split n (report.json support 합)
    "eval_source": "artifacts/classifier_p1_v5_clean/v-fe4b386b/report.json",
}


def main() -> int:
    # [P0#①-a] 이 스크립트는 activate_model_version 을 직접 호출해 deploy gate·locked-eval·감사를
    # 모두 우회한다(데모 metrics/latest 시딩 전용). 하드닝/운영 프로파일에서는 이 우회를 거부한다 —
    # 운영 활성은 POST /admin/model/activate(게이트·감사)로만.
    from koipa.config import settings  # noqa: PLC0415
    profile = (getattr(settings, "deploy_profile", "") or "").lower()
    if getattr(settings, "require_safety_gates", False) or profile in ("onprem-local", "full-train"):
        print(
            f"[REFUSED] 하드닝/운영 프로파일(deploy_profile={profile!r}, "
            f"require_safety_gates={getattr(settings, 'require_safety_gates', False)})에서는 "
            "seed_active_model_version 로 모델을 활성화할 수 없습니다 — 이 스크립트는 deploy gate·"
            "locked-eval·감사를 우회하는 데모 전용입니다. 운영 활성은 POST /admin/model/activate 사용.",
            file=sys.stderr,
        )
        return 2
    with session_scope() as db:
        repo = TrainingRepo(db)
        mv = repo.get_by_label(LABEL)
        if mv is not None:
            mv.metrics = METRICS
            mv.model_uri = MODEL_URI
            mv.base_model = BASE_MODEL
            mv.training_data_count = METRICS["sample_count"]
            print(f"[update] 기존 ModelVersion 갱신: {mv.version_label} ({mv.version_id})")
        else:
            mv = repo.register_model_version(
                version_label=LABEL,
                base_model=BASE_MODEL,
                metrics=METRICS,
                model_uri=MODEL_URI,
            )
            mv.training_data_count = METRICS["sample_count"]
            db.flush()
            print(f"[register] 신규 ModelVersion 등록: {mv.version_label} ({mv.version_id})")
        repo.activate_model_version(mv.version_id)
        print(f"[activate] 활성화 완료: {mv.version_label}  is_active=True")
    print("[done] /api/v1/metrics/latest 가 이제 이 버전의 메트릭을 반환합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

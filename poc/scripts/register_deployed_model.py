"""GA 프로덕션: 배포된 분류기를 ModelVersion 으로 DB에 '등록만' 한다(is_active=False).

프레시 온프렘 GA(enable_training=False)엔 배포 분류기를 ModelVersion 행으로 남기는 프로덕션
단계가 없다(register_and_gate_model 는 워커 학습태스크 경유만, seed_active_model_version 은
하드닝 프로파일에서 거부). 그 결과:
  - GET /api/v1/metrics/latest 가 404 "no active model"
  - POST /api/v1/admin/model/activate 가 '미등록'으로 실패
  → 관제·거버넌스 화면이 빈 상태(서빙 자체는 env CLASSIFIER_MODEL_DIR 폴백으로 무중단).

이 스크립트가 report.json 의 *실측* 메트릭을 읽어 ModelVersion 을 등록한다(멱등).
활성화는 하지 않는다 — 활성은 반드시 게이트가 있는 POST /admin/model/activate(deploy gate·
locked-eval·감사)로만 한다. 따라서 하드닝/운영 프로파일에서도 안전하다(seed_active_model_version
과 달리 게이트 우회가 없음).

실행(poc 디렉터리, .env 로드):
    .venv\\Scripts\\python.exe scripts\\register_deployed_model.py
    # 또는  ... --model-dir artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lloydk.config import settings  # noqa: E402
from lloydk.db import session_scope  # noqa: E402
from lloydk.repositories.training_repo import TrainingRepo  # noqa: E402

# 릴리스 기본 모델(단일 진실원 — build_offline_bundle --release-model 과 동일 경로).
# v5_clean/v-fe4b386b 승격(2026-07-29 3축 PASS). 롤백=classifier_p1_retrain_v4_clean/v-dd3abab9.
_RELEASE_DEFAULT = "artifacts/classifier_p1_v5_clean/v-fe4b386b"


def metrics_from_report(report: dict, report_path: Path) -> dict:
    """report.json → ModelVersion.metrics 스키마(순수 함수 — 테스트 대상).

    sample_count 는 confusion_matrix 셀 합으로 산출(report 에 명시 필드가 없어도 안전).
    하드코딩(seed_active_model_version)과 달리 런타임에 실측 report 를 읽는다(드리프트 차단).
    """
    cm = report.get("confusion_matrix") or []
    sample_count = sum(sum(row) for row in cm) if cm else report.get("sample_count")
    return {
        "accuracy": report.get("accuracy"),
        "precision_macro": report.get("precision_macro"),
        "recall_macro": report.get("recall_macro"),
        "f1_macro": report.get("f1_macro"),
        "fnr_overall": report.get("fnr_overall"),
        "fnr_by_grade": report.get("fnr_by_grade") or {},
        "sample_count": sample_count,
        "eval_source": str(report_path).replace("\\", "/"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="GA: 배포 분류기를 ModelVersion 으로 등록(활성화는 안 함)")
    default_dir = getattr(settings, "classifier_model_dir", "") or _RELEASE_DEFAULT
    ap.add_argument("--model-dir", default=default_dir, help="분류기 가중치 디렉토리(report.json 포함)")
    ap.add_argument("--report", default=None, help="메트릭 report.json (기본: <model-dir>/report.json)")
    ap.add_argument("--label", default=None, help="version_label (기본: report.model_version 또는 디렉토리명)")
    ap.add_argument(
        "--base-model",
        default=getattr(settings, "classifier_base_model", "") or "kakaobank/kf-deberta-base",
    )
    args = ap.parse_args()

    model_dir = Path(args.model_dir)
    report_path = Path(args.report) if args.report else model_dir / "report.json"
    if not report_path.exists():
        print(f"[ERR] report.json 없음: {report_path} — 등록할 실측 메트릭이 없습니다.", file=sys.stderr)
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    label = (args.label or report.get("model_version") or model_dir.name)[:50]
    metrics = metrics_from_report(report, report_path)
    model_uri = str(model_dir).replace("\\", "/")

    with session_scope() as db:
        repo = TrainingRepo(db)
        mv = repo.get_by_label(label)
        if mv is not None:
            # 멱등 — 메트릭/URI 갱신만. is_active 는 절대 건드리지 않는다(활성은 게이트 경유).
            mv.metrics = metrics
            mv.model_uri = model_uri
            mv.base_model = args.base_model
            mv.training_data_count = metrics.get("sample_count")
            print(f"[update] 기존 ModelVersion 갱신(활성상태 불변): {mv.version_label}  is_active={mv.is_active}")
        else:
            mv = repo.register_model_version(
                version_label=label,
                base_model=args.base_model,
                metrics=metrics,
                model_uri=model_uri,
            )
            mv.training_data_count = metrics.get("sample_count")
            db.flush()
            print(f"[register] 신규 ModelVersion 등록: {mv.version_label}  is_active={mv.is_active} (비활성)")

    print(
        "[done] 등록 완료(활성화 안 함). 관제·거버넌스 화면(metrics/latest)을 채우려면 게이트를 거쳐 활성화:\n"
        "       POST /api/v1/admin/model/activate  (deploy gate·locked-eval·감사)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

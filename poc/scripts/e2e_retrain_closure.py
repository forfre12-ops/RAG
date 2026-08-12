"""E2E — A2 능동학습 재학습 폐곡선 실측 (rebuild→train(real)→register→gate→consume).

실 KF-DeBERTa 1에폭 + 실 Postgres로 `train_classifier_task` 전체 경로를 검증한다.
MLflow는 로컬 파일추적(서버/MinIO 불요), base는 캐시된 kakaobank/kf-deberta-base, 소형
데이터·1에폭이라 분 단위. 시드 교정 1건이 학습에 반영·소비되는지, ModelVersion이 등록되고
배포 게이트가 평가되는지 확인한다. 끝나면 시드/모델/데이터셋/mlruns 정리(finally).

실행: python scripts/e2e_retrain_closure.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# MLflow 로컬 파일추적 강제 (서버/MinIO 의존 제거).
os.environ["MLFLOW_TRACKING_URI"] = "file:" + (_ROOT / "mlruns_e2e").as_posix()

TAG = uuid.uuid4().hex[:8]
DATA_DIR = _ROOT / "datasets" / f"e2e_{TAG}"
OUT_DIR = _ROOT / "artifacts" / f"e2e_{TAG}"
LABELS = ["TS", "S1", "S2", "S3"]


def build_tiny_dataset() -> dict:
    import json as _j
    src = _ROOT / "datasets" / "labeled_bpilot" / "val.jsonl"
    by_label: dict[str, list[dict]] = defaultdict(list)
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = _j.loads(line)
        if r.get("label") in LABELS and r.get("text"):
            by_label[r["label"]].append({"text": r["text"], "label": r["label"]})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    splits = {"train": 6, "val": 2, "test": 2}
    paths = {}
    cursor = defaultdict(int)
    for split, per in splits.items():
        rows = []
        for lab in LABELS:
            pool = by_label[lab]
            rows += pool[cursor[lab]:cursor[lab] + per]
            cursor[lab] += per
        p = DATA_DIR / f"{split}.jsonl"
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        paths[split] = str(p)
    return paths


def seed_correction():
    """doc(+chunk text) + classification(S3) + correction(→TS) 시드. 반환: ids dict."""
    from koipa.db import SessionLocal
    from koipa.db.models import (
        Chunk, Classification, ClassificationLevel, Correction, Document, Tenant,
    )
    s = SessionLocal()
    try:
        tid = f"e2e-{TAG}"
        s.add(Tenant(tenant_id=tid, name=tid))
        s.flush()
        levels = {lv.level_code: lv.level_id for lv in s.query(ClassificationLevel).all()}
        doc = Document(tenant_id=tid, filename="e2e.pdf", source_format="pdf")
        s.add(doc)
        s.flush()
        s.add(Chunk(doc_id=doc.doc_id, tenant_id=tid, chunk_index=0,
                    content="마스터 키 롤링 절차와 HSM 파라미터 — 사외 유출 금지 영업비밀.",
                    token_count=12, char_count=40))
        cls = Classification(doc_id=doc.doc_id, tenant_id=tid, model_version="e2e-pred",
                             predicted_level_id=levels["S3"], confidence=0.9, alternatives=[])
        s.add(cls)
        s.flush()
        corr = Correction(classification_id=cls.classification_id,
                          original_level_id=levels["S3"], corrected_level_id=levels["TS"],
                          direction="underclass", corrected_by="e2e-reviewer")
        s.add(corr)
        s.flush()
        s.commit()
        return {"tenant_id": tid, "doc_id": doc.doc_id,
                "classification_id": cls.classification_id, "correction_id": corr.correction_id}
    finally:
        s.close()


def cleanup(seed, run_id, version_label):
    from koipa.db import session_scope
    from koipa.db.models import (
        Chunk, Classification, Correction, Document, ModelVersion, Tenant, TrainingRun,
    )
    try:
        with session_scope() as db:
            if seed:
                db.query(Correction).filter_by(classification_id=seed["classification_id"]).delete()
                db.query(Classification).filter_by(classification_id=seed["classification_id"]).delete()
                db.query(Chunk).filter_by(doc_id=seed["doc_id"]).delete()
                db.query(Document).filter_by(doc_id=seed["doc_id"]).delete()
                db.query(Tenant).filter_by(tenant_id=seed["tenant_id"]).delete()
            if version_label:
                db.query(ModelVersion).filter_by(version_label=version_label).delete()
            if run_id:
                db.query(TrainingRun).filter_by(run_id=run_id).delete()
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] DB cleanup warn: {exc}")
    for d in (DATA_DIR, OUT_DIR, _ROOT / "mlruns_e2e"):
        shutil.rmtree(d, ignore_errors=True)


def main() -> int:
    print(f"=== E2E retrain closure (tag={TAG}) ===")
    paths = build_tiny_dataset()
    print(f"tiny dataset: {paths}")
    seed = seed_correction()
    print(f"seeded correction id={seed['correction_id']} (S3→TS, e2e-reviewer)")

    from koipa.workers.tasks import train_classifier_task

    spec_kwargs = {
        "base_model": "kakaobank/kf-deberta-base",
        "train_path": paths["train"], "val_path": paths["val"], "test_path": paths["test"],
        "output_dir": str(OUT_DIR), "epochs": 1, "batch_size": 4,
        "use_mlflow": True, "experiment_name": f"e2e-{TAG}",
    }
    run_id = None
    version_label = None
    ok = True
    try:
        print("=== running train_classifier_task (real KF-DeBERTa, 1 epoch) ... ===")
        out = train_classifier_task(spec_kwargs=spec_kwargs)
        deploy = out.get("deploy", {})
        version_label = deploy.get("version_label")
        run_id = out.get("corrections_run_id")

        print("\n=== RESULT ===")
        print(json.dumps({
            "model_version": out.get("model_version"),
            "fnr_high": out.get("fnr_high"),
            "f1_macro": out.get("f1_macro"),
            "corrections_incorporated": out.get("corrections_incorporated"),
            "corrections_consumed": out.get("corrections_consumed"),
            "deploy": {k: deploy.get(k) for k in ("registered", "activated", "version_label")},
            "gate_passed": (deploy.get("gate") or {}).get("passed"),
        }, ensure_ascii=False, indent=2))

        # ── 검증 ───────────────────────────────────────────────────────────
        checks = {
            "교정 반영(incorporated>=1)": out.get("corrections_incorporated", 0) >= 1,
            "교정 소비(consumed>=1)": out.get("corrections_consumed", 0) >= 1,
            "모델 등록(registered)": deploy.get("registered") is True,
            "기본 비활성(auto_activate=False)": deploy.get("activated") is False,
            "배포 게이트 평가됨": isinstance(deploy.get("gate"), dict) and "passed" in deploy["gate"],
        }
        # DB 확인: 교정이 실제로 consumed_in_run 마킹됐는지 + ModelVersion 존재
        from koipa.db import session_scope
        from koipa.db.models import Correction, ModelVersion
        with session_scope() as db:
            c = db.get(Correction, seed["correction_id"])
            checks["DB: 교정 consumed_in_run 마킹"] = c is not None and c.consumed_in_run is not None
            if version_label:
                mv = db.query(ModelVersion).filter_by(version_label=version_label).first()
                checks["DB: ModelVersion 등록됨"] = mv is not None

        print("\n=== CHECKS ===")
        for k, v in checks.items():
            print(f"  [{'PASS' if v else 'FAIL'}] {k}")
            ok = ok and v
        print(f"\n=== {'ALL PASS' if ok else 'SOME FAILED'} ===")
        return 0 if ok else 1
    finally:
        cleanup(seed, run_id, version_label)
        print("[cleanup] seed/model/dataset/mlruns removed")


if __name__ == "__main__":
    raise SystemExit(main())

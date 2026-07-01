"""Optuna 게이트·온도 튜닝 드라이버 — P1 (스켈레톤).

골든셋으로 후처리 노브(온도·게이트·escalation τ)를 자동 탐색한다.
maximize F1 subject to FNR_고등급(TS·S1) ≤ target. 재학습 0 · CPU · 무GPU.
평가는 기존 evaluate_via_serving 재사용(= eval_serving_path.py와 동일 서빙경로).

사용:
  python scripts/optuna_tune_gates.py \
    --model-dir artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9 \
    --gold datasets/gold_real/holdout_eval.clean.jsonl \
    --n-trials 50 --fnr-high-target 0.0 \
    --report reports/optuna_gate_tuning.md

전제: 정밀 튜닝은 **1,000 사람검수 골든**에서. holdout(77건)은 파이프라인 검증용(과적합 주의).
의존성: optuna (`pip install optuna` — 폐쇄망은 wheel vendoring).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

LABELS = ["TS", "S1", "S2", "S3"]


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        r = json.loads(line)
        label = r.get("label") or r.get("expected_grade") or r.get("target")
        text = r.get("text") or r.get("body") or ""
        if label in LABELS and text:
            rows.append({"text": text, "label": label})
    return rows


def split_train_test(rows: list[dict], test_frac: float = 0.3, seed: int = 42):
    """등급 층화 split — val(튜닝) / test(무편향 검증)."""
    import random

    by_grade: dict[str, list[dict]] = {}
    for r in rows:
        by_grade.setdefault(r["label"], []).append(r)
    rng = random.Random(seed)
    train: list[dict] = []
    test: list[dict] = []
    for _g, items in by_grade.items():
        items = items[:]
        rng.shuffle(items)
        k = max(1, int(len(items) * test_frac)) if len(items) > 1 else 0
        test.extend(items[:k])
        train.extend(items[k:])
    return train, test


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--gold", default="datasets/gold_real/holdout_eval.clean.jsonl")
    ap.add_argument("--n-trials", type=int, default=50)
    ap.add_argument("--fnr-high-target", type=float, default=0.0)
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--report", default="reports/optuna_gate_tuning.md")
    args = ap.parse_args()

    try:
        import optuna  # noqa: F401,PLC0415
    except ImportError:
        print("optuna 미설치 — `pip install optuna` (폐쇄망은 wheel vendoring) 후 재실행.", file=sys.stderr)
        return 2

    from optuna.trial import FixedTrial  # noqa: PLC0415

    from lloydk.modules.m6_evaluation.optuna_tuner import (  # noqa: PLC0415
        TuneConfig,
        make_objective,
        run_study,
    )

    gold = Path(args.gold)
    if not gold.is_absolute():
        gold = _HERE.parent / gold
    rows = load_rows(gold)
    train, test = split_train_test(rows, test_frac=args.test_frac, seed=args.seed)
    print(f"golden n={len(rows)} → tune(val)={len(train)} / test={len(test)}")
    if len(train) < 30:
        print("⚠️ 튜닝셋이 작다(<30) — 과적합 위험. 1,000 사람골든에서 본 튜닝 권장.", file=sys.stderr)

    model_dir = Path(args.model_dir)
    if not model_dir.is_absolute():
        model_dir = _HERE.parent / model_dir

    cfg = TuneConfig(
        model_dir=str(model_dir),
        fnr_high_target=args.fnr_high_target,
        n_trials=args.n_trials,
        seed=args.seed,
    )
    study = run_study(train, cfg)
    best = study.best_trial

    # 무편향 검증 — best 노브를 test split에 적용해 재측정(과적합 점검).
    test_metrics = None
    if test:
        obj = make_objective(test, cfg)
        fixed = FixedTrial(best.params)
        obj(fixed)  # user_attrs 채움
        test_metrics = {k: fixed.user_attrs.get(k) for k in ("f1_macro", "accuracy", "fnr_high", "fnr_overall")}

    out = Path(args.report)
    if not out.is_absolute():
        out = _HERE.parent / out
    out.parent.mkdir(parents=True, exist_ok=True)
    md = [
        "# Optuna 게이트·온도 튜닝 결과 (P1)",
        "",
        f"- model_dir: `{args.model_dir}`",
        f"- gold: `{args.gold}`  (n={len(rows)} → val {len(train)} / test {len(test)})",
        f"- n_trials: {args.n_trials} · FNR_고등급 제약 ≤ {args.fnr_high_target}",
        "",
        "## Best (val)",
        f"- objective: {best.value:.4f}",
        f"- params: `{json.dumps(best.params, ensure_ascii=False)}`",
        f"- val metrics: `{json.dumps(best.user_attrs, ensure_ascii=False)}`",
    ]
    if test_metrics:
        md += [
            "",
            "## 무편향 검증 (test split)",
            f"- test metrics: `{json.dumps(test_metrics, ensure_ascii=False)}`",
            "> val↔test 격차가 크면 과적합 — 골든 N을 늘려라.",
        ]
    out.write_text("\n".join(md), encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps(
            {
                "model_dir": args.model_dir,
                "gold": args.gold,
                "n": len(rows),
                "best_value": best.value,
                "best_params": best.params,
                "best_val_metrics": best.user_attrs,
                "test_metrics": test_metrics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(
        {"best_params": best.params, "best_val": best.user_attrs, "test": test_metrics, "report": str(out)},
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

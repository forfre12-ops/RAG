"""A/B 다중 seed 검증 — ab_qwen_gemma_s1.py 결과의 통계적 견고성 확인.

단일 seed(42) 결과: A1(+Qwen) S1 0.25 vs A2(+Gemma) S1 0.0 — 차이 3건이라 noisy.
같은 arm 학습셋(datasets/ab_s1/train_A{0,1,2}.jsonl)·val·holdout 고정,
**학습 seed만** 42/43/44/45로 바꿔 재학습·평가 → arm별 S1 recall·F1·FNR 평균±편차.

seed 42는 기존 reports/ab_s1/{arm}.json 재사용. 43/44/45만 신규 학습(각 별도 프로세스).
resumable: 학습된 model_dir·eval 리포트 있으면 skip.

실행:
  PYTHONUTF8=1 .venv/Scripts/python.exe scripts/ab_multiseed.py
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

PY = sys.executable
WORK = Path("datasets/ab_s1")
ARTROOT = Path("artifacts/ab_s1")
REPORTS = Path("reports/ab_s1")
HOLDOUT = Path("datasets/gold_real/holdout_eval.clean.jsonl")
VAL = WORK / "val.jsonl"

ARMS = {"A0_base": "train_A0.jsonl", "A1_qwen": "train_A1.jsonl", "A2_gemma": "train_A2.jsonl"}
SEED_BASE = 42                       # 기존 결과 재사용
SEEDS_NEW = [43, 44, 45]             # 신규 학습
EPOCHS, SEQ = 4, 256


def log(m: str) -> None:
    print(f"[ms][{time.strftime('%H:%M:%S')}] {m}", flush=True)


def _find_model_dir(out_dir: Path):
    c = sorted(out_dir.glob("v-*/model.safetensors"))
    return c[-1].parent if c else None


def train(arm: str, seed: int) -> str:
    out_dir = ARTROOT / f"{arm}_s{seed}"
    md = _find_model_dir(out_dir)
    if md is not None:
        log(f"train[{arm} s{seed}] skip ({md.name})")
        return str(md)
    log(f"train[{arm} s{seed}] 시작")
    t0 = time.time()
    r = subprocess.run(
        [PY, "scripts/p1_train_classifier.py", "--mode", "full",
         "--train-path", str(WORK / ARMS[arm]), "--val-path", str(VAL),
         "--test-path", str(HOLDOUT), "--output-dir", str(out_dir),
         "--epochs", str(EPOCHS), "--max-seq-len", str(SEQ),
         "--batch-size", "16", "--no-mlflow", "--seed", str(seed)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600,
    )
    md = _find_model_dir(out_dir)
    if md is None:
        log(f"train[{arm} s{seed}] FAIL\n{r.stderr[-800:]}")
        raise RuntimeError(f"train {arm} s{seed} failed")
    log(f"train[{arm} s{seed}] done {time.time()-t0:.0f}s -> {md.name}")
    return str(md)


def evaluate(arm: str, seed: int, model_dir: str) -> dict:
    rep = REPORTS / f"{arm}_s{seed}.json"
    if not rep.exists():
        subprocess.run(
            [PY, "scripts/eval_p1_holdout.py", "--model-dir", model_dir,
             "--holdout", str(HOLDOUT), "--report", str(rep)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
    d = json.loads(rep.read_text(encoding="utf-8"))["ALL"]
    return {"f1": d["f1_macro"], "fnr": d["fnr_underclass"],
            "s1": d["per_class_recall"]["S1"], "ts": d["per_class_recall"]["TS"]}


def seed42(arm: str) -> dict:
    d = json.loads((REPORTS / f"{arm}.json").read_text(encoding="utf-8"))["ALL"]
    return {"f1": d["f1_macro"], "fnr": d["fnr_underclass"],
            "s1": d["per_class_recall"]["S1"], "ts": d["per_class_recall"]["TS"]}


def main() -> int:
    log(f"=== multiseed: base={SEED_BASE}(reuse) new={SEEDS_NEW} epochs={EPOCHS} ===")
    per_arm: dict[str, list[dict]] = {a: [] for a in ARMS}

    # seed 42 재사용
    for arm in ARMS:
        m = seed42(arm)
        per_arm[arm].append({"seed": SEED_BASE, **m})
        log(f"reuse[{arm} s42] s1={m['s1']} f1={m['f1']}")

    # 신규 seed: arm 바깥 루프=seed (GPU 캐시 일관). 각 arm 학습+평가.
    for seed in SEEDS_NEW:
        for arm in ARMS:
            md = train(arm, seed)
            m = evaluate(arm, seed, md)
            per_arm[arm].append({"seed": seed, **m})
            log(f"eval[{arm} s{seed}] s1={m['s1']} f1={m['f1']} fnr={m['fnr']}")

    # 집계
    def agg(arm: str, key: str):
        vals = [r[key] for r in per_arm[arm]]
        mean = statistics.mean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
        return mean, sd, vals

    summary = {"seeds": [SEED_BASE] + SEEDS_NEW, "epochs": EPOCHS, "per_arm_runs": per_arm, "agg": {}}
    log("================ MULTISEED RESULT (mean ± sd, n=%d) ================" % (1 + len(SEEDS_NEW)))
    log(f"{'arm':10s} {'S1 recall':>20s} {'F1-macro':>20s} {'FNR':>16s}")
    for arm in ARMS:
        s1m, s1sd, s1v = agg(arm, "s1")
        f1m, f1sd, _ = agg(arm, "f1")
        fnm, fnsd, _ = agg(arm, "fnr")
        summary["agg"][arm] = {
            "s1_mean": round(s1m, 4), "s1_sd": round(s1sd, 4), "s1_vals": s1v,
            "f1_mean": round(f1m, 4), "f1_sd": round(f1sd, 4),
            "fnr_mean": round(fnm, 4), "fnr_sd": round(fnsd, 4),
        }
        log(f"{arm:10s} {s1m:6.3f} ± {s1sd:5.3f} {str(s1v):>7s}"
            f"   {f1m:6.3f} ± {f1sd:5.3f}   {fnm:6.3f} ± {fnsd:5.3f}")

    out = REPORTS / "summary_multiseed.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    # 판정 보조: A1(qwen) vs A2(gemma) S1 평균차 / 편차
    a1, a2 = summary["agg"]["A1_qwen"], summary["agg"]["A2_gemma"]
    diff = a1["s1_mean"] - a2["s1_mean"]
    pooled = (a1["s1_sd"] + a2["s1_sd"]) / 2 or 1e-9
    log(f"A1(qwen) - A2(gemma) S1 평균차 = {diff:+.3f}  (pooled sd {pooled:.3f}, 비율 {diff/pooled:+.2f})")
    log(f"summary -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

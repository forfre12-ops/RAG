"""A/B: fnr_cost_multiplier(고등급 미탐 벌점)가 S1 recall을 올리는가?

같은 base(datasets/ab_s1/train_A0.jsonl = train_subset 550, 합성 없음)·val·holdout 고정,
**--fnr-cost-multiplier만** 1.0/2.0/3.0 으로 바꿔 4 seed(42-45) 재학습·평가.
multiplier 1.0 = 기존 A0_base 4-seed 결과 재사용(reports/ab_s1/A0_base*.json).

핵심 관찰: S1·TS recall↑ 하면서 S2·S3 recall이 얼마나 무너지는가(과분류 비용).
resumable. 실행:
  PYTHONUTF8=1 .venv/Scripts/python.exe scripts/ab_fnrcost.py
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

PY = sys.executable
WORK = Path("datasets/ab_s1")
ART = Path("artifacts/ab_fnr")
REP = Path("reports/ab_fnr")
REP_BASE = Path("reports/ab_s1")          # multiplier 1.0 재사용 위치
HOLDOUT = Path("datasets/gold_real/holdout_eval.clean.jsonl")
TRAIN = WORK / "train_A0.jsonl"
VAL = WORK / "val.jsonl"

MULTS_NEW = [2.0, 3.0]
SEEDS = [42, 43, 44, 45]
EPOCHS, SEQ = 4, 256


def log(m: str) -> None:
    print(f"[fnr][{time.strftime('%H:%M:%S')}] {m}", flush=True)


def _find_model_dir(out_dir: Path):
    c = sorted(out_dir.glob("v-*/model.safetensors"))
    return c[-1].parent if c else None


def _metrics(report_path: Path) -> dict:
    d = json.loads(report_path.read_text(encoding="utf-8"))["ALL"]
    pc = d["per_class_recall"]
    return {"f1": d["f1_macro"], "fnr": d["fnr_underclass"],
            "to_s3": d.get("high_risk_to_s3", 0),
            "TS": pc["TS"], "S1": pc["S1"], "S2": pc["S2"], "S3": pc["S3"]}


def base_metrics(seed: int) -> dict:
    p = REP_BASE / ("A0_base.json" if seed == 42 else f"A0_base_s{seed}.json")
    return _metrics(p)


def run(mult: float, seed: int) -> dict:
    tag = f"m{mult}_s{seed}"
    out_dir = ART / tag
    rep = REP / f"{tag}.json"
    md = _find_model_dir(out_dir)
    if md is None:
        log(f"train[{tag}] 시작")
        t0 = time.time()
        r = subprocess.run(
            [PY, "scripts/p1_train_classifier.py", "--mode", "full",
             "--train-path", str(TRAIN), "--val-path", str(VAL),
             "--test-path", str(HOLDOUT), "--output-dir", str(out_dir),
             "--epochs", str(EPOCHS), "--max-seq-len", str(SEQ),
             "--batch-size", "16", "--no-mlflow", "--seed", str(seed),
             "--fnr-cost-multiplier", str(mult)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600,
        )
        md = _find_model_dir(out_dir)
        if md is None:
            log(f"train[{tag}] FAIL\n{r.stderr[-800:]}")
            raise RuntimeError(f"train {tag} failed")
        log(f"train[{tag}] done {time.time()-t0:.0f}s -> {md.name}")
    if not rep.exists():
        subprocess.run(
            [PY, "scripts/eval_p1_holdout.py", "--model-dir", str(md),
             "--holdout", str(HOLDOUT), "--report", str(rep)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
    m = _metrics(rep)
    log(f"eval[{tag}] S1={m['S1']} TS={m['TS']} S2={m['S2']} S3={m['S3']} f1={m['f1']} toS3={m['to_s3']}")
    return m


def main() -> int:
    REP.mkdir(parents=True, exist_ok=True)
    log(f"=== fnr_cost A/B: mult 1.0(reuse)+{MULTS_NEW}  seeds={SEEDS} ===")
    runs: dict[str, list[dict]] = {}

    # multiplier 1.0 재사용
    runs["1.0"] = [base_metrics(s) for s in SEEDS]
    for s, m in zip(SEEDS, runs["1.0"]):
        log(f"reuse[m1.0 s{s}] S1={m['S1']} f1={m['f1']}")

    for mult in MULTS_NEW:
        runs[str(mult)] = [run(mult, s) for s in SEEDS]

    def agg(mult: str, key: str):
        vals = [r[key] for r in runs[mult]]
        return statistics.mean(vals), (statistics.stdev(vals) if len(vals) > 1 else 0.0), vals

    summary = {"seeds": SEEDS, "epochs": EPOCHS, "runs": runs, "agg": {}}
    log("============ FNR-COST RESULT (mean ± sd, n=4) ============")
    log(f"{'mult':5s} {'S1':>14s} {'TS':>14s} {'S2':>14s} {'S3':>14s} {'F1':>14s} {'toS3':>6s}")
    for mult in ["1.0"] + [str(m) for m in MULTS_NEW]:
        a = {k: agg(mult, k) for k in ("S1", "TS", "S2", "S3", "f1", "fnr", "to_s3")}
        summary["agg"][mult] = {k: {"mean": round(v[0], 4), "sd": round(v[1], 4), "vals": v[2]}
                                for k, v in a.items()}
        log(f"{mult:5s} "
            f"{a['S1'][0]:5.2f}±{a['S1'][1]:4.2f} "
            f"{a['TS'][0]:5.2f}±{a['TS'][1]:4.2f} "
            f"{a['S2'][0]:5.2f}±{a['S2'][1]:4.2f} "
            f"{a['S3'][0]:5.2f}±{a['S3'][1]:4.2f} "
            f"{a['f1'][0]:5.3f}±{a['f1'][1]:4.2f} "
            f"{a['to_s3'][0]:5.1f}")
    out = REP / "summary_fnrcost.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"summary -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""합성 silver로 KF-DeBERTa 학습 → gold_real(실 문서) holdout 평가 = 합성→실 일반화 측정.

흐름: silver build_*.jsonl → (text,label) train/val 결정론적 층화분할 → p1_train_classifier
--mode full(KF-DeBERTa, fnr_cost) → eval_p1_model_gold(실 holdout 평가). 각 단계 subprocess·
로그·실패캡처. 결과 work_dir/summary.json.

합성≠실(qwen 생성 vs 실 한국어 문서)이라 train/eval 누출 없음 = 깨끗한 일반화 테스트.
gold_real 라벨은 Qwen 판정(사람서명 아님)이라 평가도 pseudo-gold — '실데이터 일반화 신호'이지
절대 진실 아님. 헤드라인 F1로 보고 금지(설계 전제).

사용: python train_eval_synth_silver.py --silver-run <run_dir> [--gold datasets/gold_real/holdout_eval.clean.jsonl]
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parents[1]  # poc/
PY = sys.executable
SRC = POC / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def load(p):
    return [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]


def _record_key(r: dict) -> tuple[str, str]:
    return (str(r.get("doc_id") or ""), str(r.get("text_hash") or ""))


def _non_train_keys(gold_path: str | Path) -> set[tuple[str, str]]:
    """학습에서 제외할 gold 키 = 학습 불가 tier(LOCKED 평가정답 + HELD 격리).

    denylist(`== TIER_LOCKED`)를 allowlist(`not in TRAIN_TIERS`)로 전환 — de-locked
    human_review가 HELD가 되며 학습으로 새어들던 역-누수를 차단(golden_tiers TRAIN_TIERS와 정합)."""
    from lloydk.golden_tiers import TRAIN_TIERS, tier_of

    if not Path(gold_path).exists():
        return set()
    return {
        _record_key(r)
        for r in load(gold_path)
        if tier_of(r) not in TRAIN_TIERS and any(_record_key(r))
    }


def convert_and_split(silver_run, work_dir, val_frac=0.15, *, gold_path=None, allow_build_fallback=False):
    """silver build_*.jsonl → (text,label) train/val. 결정론적 층화(random 없음)."""
    run_dir = Path(silver_run)
    split_path = run_dir / "silver_train.jsonl"
    if split_path.exists():
        source_rows = load(split_path)
    elif allow_build_fallback:
        source_rows = []
        for f in glob.glob(os.path.join(glob.escape(str(run_dir)), "build_*.jsonl")):
            source_rows += load(f)
    else:
        raise FileNotFoundError(
            f"{split_path} not found. Run scripts/run_golden_split_signoff.py first "
            "or pass --allow-build-fallback explicitly."
        )
    excluded_keys = _non_train_keys(gold_path) if gold_path else set()
    rows = [
        {"text": r["text"], "label": r.get("label") or r.get("llm_grade")}
        for r in source_rows
        if (r.get("label") or r.get("llm_grade"))
        and r.get("text")
        and _record_key(r) not in excluded_keys
    ]
    by = {}
    for r in rows:
        by.setdefault(r["label"], []).append(r)
    train, val = [], []
    for lbl, items in sorted(by.items()):
        items = sorted(items, key=lambda r: r["text"])  # 결정론적 순서
        # 클래스가 학습행 0이 되지 않게: 1건이면 전부 train, ≥2면 val은 최대 len-1까지만.
        k = 0 if len(items) < 2 else min(len(items) - 1, max(1, int(round(len(items) * val_frac))))
        val += items[:k]
        train += items[k:]
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    def dump(name, data):
        with io.open(Path(work_dir) / name, "w", encoding="utf-8") as f:
            for r in data:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    dump("train.jsonl", train)
    dump("val.jsonl", val)
    return {"train_n": len(train), "val_n": len(val),
            "label_dist": dict(Counter(r["label"] for r in rows))}


def run_step(name, cmd, log_path):
    """subprocess 실행, stdout/stderr를 로그파일로. (rc, tail) 반환."""
    with io.open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.run(cmd, cwd=str(POC), stdout=lf, stderr=subprocess.STDOUT, text=True)
    tail = ""
    try:
        tail = "".join(io.open(log_path, encoding="utf-8", errors="replace").readlines()[-25:])
    except Exception:  # noqa: BLE001
        pass
    return proc.returncode, tail


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--silver-run", required=True, help="golden_runs/<run_id> 디렉토리")
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl",
                    help="실 평가셋(POC 상대경로). 합성모델은 실데이터와 누출 없어 전체 777 사용(등급별 N 견고). "
                         "clean holdout(42)은 --gold로 오버라이드.")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--fnr-cost", type=float, default=2.0)
    ap.add_argument("--max-seq-len", type=int, default=512)
    ap.add_argument(
        "--allow-build-fallback",
        action="store_true",
        help="Unsafe compatibility mode: read build_*.jsonl when silver_train.jsonl is missing.",
    )
    args = ap.parse_args(argv)

    silver_run = args.silver_run
    run_id = Path(silver_run).name
    work_dir = (Path(args.work_dir).resolve() if args.work_dir
                else (POC / "artifacts" / f"synth_train_{run_id}"))
    work_dir.mkdir(parents=True, exist_ok=True)
    model_dir = work_dir / "model"
    summary = {"silver_run": silver_run, "work_dir": str(work_dir), "steps": {}}

    # 1) 변환·분할
    try:
        conv = convert_and_split(
            silver_run,
            work_dir,
            gold_path=args.gold,
            allow_build_fallback=args.allow_build_fallback,
        )
        summary["convert"] = conv
        print(f"[1/3 convert] train={conv['train_n']} val={conv['val_n']} dist={conv['label_dist']}",
              file=sys.stderr, flush=True)
    except Exception as exc:  # noqa: BLE001
        summary["steps"]["convert"] = f"ERROR {type(exc).__name__}: {exc}"
        _save(summary, work_dir)
        return 2

    # 2) 학습 (KF-DeBERTa, test_path=실 holdout → 트레이너가 실 일반화도 직접 평가)
    train_cmd = [
        PY, "scripts/p1_train_classifier.py", "--mode", "full",
        "--train-path", str(work_dir / "train.jsonl"),
        "--val-path", str(work_dir / "val.jsonl"),
        "--test-path", args.gold,
        "--output-dir", str(model_dir),
        "--no-mlflow", "--epochs", str(args.epochs),
        "--fnr-cost-multiplier", str(args.fnr_cost),
        "--max-seq-len", str(args.max_seq_len),
    ]
    print(f"[2/3 train] {' '.join(train_cmd)}", file=sys.stderr, flush=True)
    rc, tail = run_step("train", train_cmd, work_dir / "train.log")
    summary["steps"]["train"] = {"rc": rc, "tail": tail}
    if rc != 0:
        print(f"[train FAIL rc={rc}]\n{tail}", file=sys.stderr)
        _save(summary, work_dir)
        return 3

    # 3) 실 holdout 상세 평가(FNR-safe + 오류분석)
    eval_cmd = [
        PY, "scripts/eval_p1_model_gold.py", "--model-dir", str(model_dir),
        "--gold", args.gold, "--mode", "direct",
        "--report", str(work_dir / "gold_eval.md"),
    ]
    print(f"[3/4 eval] {' '.join(eval_cmd)}", file=sys.stderr, flush=True)
    rc, tail = run_step("eval", eval_cmd, work_dir / "eval.log")
    summary["steps"]["eval"] = {"rc": rc, "tail": tail}

    # 결과 취합(raw 모델)
    ej = work_dir / "gold_eval.json"
    if ej.exists():
        try:
            summary["gold_eval_metrics"] = json.load(io.open(ej, encoding="utf-8")).get("metrics")
        except Exception:  # noqa: BLE001
            pass

    # 4) 서빙경로 평가 — escalation τ 스윕이 raw FNR을 얼마나 회복하나(배포 실제 성능).
    #    raw 모델이 합성 천장으로 고등급 FNR 높아도, 서빙 게이트(τ→검수 에스컬레이션)가
    #    회복하면 '배포 가능'. 이게 "쓸 만한가"의 진짜 답.
    serve_report = work_dir / "serving_eval.md"
    serve_cmd = [
        PY, "scripts/eval_serving_path.py", "--model-dir", str(model_dir),
        "--gold", args.gold, "--taus", "0,0.35,0.25,0.15",
        "--report", str(serve_report),
    ]
    print(f"[4/4 serving] {' '.join(serve_cmd)}", file=sys.stderr, flush=True)
    rc, tail = run_step("serving", serve_cmd, work_dir / "serving.log")
    summary["steps"]["serving"] = {"rc": rc, "tail": tail}
    sj = serve_report.with_suffix(".json")
    if sj.exists():
        try:
            summary["serving_eval"] = json.load(io.open(sj, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass

    _save(summary, work_dir)
    print(f"[done] -> {work_dir}/summary.json", file=sys.stderr)
    print(str(work_dir / "summary.json"))
    return 0


def _save(summary, work_dir):
    with io.open(Path(work_dir) / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())

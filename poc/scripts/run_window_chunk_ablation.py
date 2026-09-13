#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""조각을 **창 안에 넣어** 학습하면 미탐이 줄어드는가 — 조건 2 × 시드 3판 대조.

## 왜 이 실험인가 (2026-09-13 실측)

학습기는 본문을 `max_seq_len`(512) 토큰에서 **자르고** 한 번 통과시킨다. 서빙은 자르지
않고 창으로 나눠 각각 통과시킨 뒤 평균 낸다. 두 경로가 다른 것은 의도된 것이다 —
뒷부분의 비밀을 놓치지 않으려는 FNR 보호다(`pipeline.py` `#chunk-trunc`).

그래서 **학습은 뒷부분을 아예 못 본다.** 정본 학습셋(`labeled_p1_v5_clean/train.jsonl`
2,042행) 실측:

    한국어 글자/토큰 비 중앙값 2.16  ->  512토큰 ≈ 1,107자
    512토큰을 넘는 행 656건 = 32.1%   ->  그 뒷부분이 학습에서 버려진다

`chunk_expand` 는 이걸 고치라고 있는 옵션인데, 조각 크기 기본값이 `max_seq_len*3`
= **1,536자**다. 창(1,107자)보다 크다. 그래서 펼쳐도 잘림이 남는다:

    조각크기   학습행수   배수     512토큰 초과 조각
      1536자    2,862   1.40배   1,217 (42.5%)   <- 기본값
      1100자    3,740   1.83배     896 (24.0%)
      1000자    4,071   1.99배     352 ( 8.6%)
       800자    4,655   2.28배       2 ( 0.0%)   <- 잘림 없음

⚠ 2026-09-12 에 `--chunk-expand` 를 시험해 "확장이 아예 안 일어난다"고 닫았는데, 그건
  **LLM 코퍼스**에서였다(문서가 짧아 조각 1개). 정본 학습셋에서는 기본값으로도 1.40배
  확장이 일어나고, 그런데도 42.5%가 잘린다. **조각을 창 안에 넣는 조합은 재본 적이 없다.**

## 어떻게 재는가

    조건 A (기준)   현행 그대로 — 문서 단위, 512토큰에서 자름
    조건 B (검증)   --chunk-expand --chunk-char-size 800 — 잘림 0

⚠ **한 판으로는 비교가 안 된다.** 같은 시드 재학습에 미탐이 83 -> 29 로 움직인 전례가
  있다([[single-training-run-is-not-a-comparison-2026-09-11]]). 조건마다 시드 3판을
  돌려 **범위**로 보고한다. 판이 겹치면 "차이 없음"이다.

⚠ **채점은 서빙 경로로 한다.** 학습 보고서 F1 은 배포 시스템 값이 아니다 — 같은 모델·
  같은 셋에서 14.4%p 벌어진 적이 있다([[training-report-f1-is-not-serving-value-2026-09-13]]).
  여기서는 `eval_p1_model_gold.predict_api_like` 로 규칙·게이트까지 포함한 전체 파이프라인을
  태운다.

## 판정 기준

요건은 PMR-002 "미탐 최소화"다. 그래서 1순위는 **무음 미탐**(정답이 고등급인데 더 낮게
예측)이고, 정확도는 참고로만 낸다. 과탐(S3 를 올려 보는 것)도 같이 내 — 미탐만 줄이고
과탐을 늘린 것은 이득이 아니다.

사용:

    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/run_window_chunk_ablation.py
    ... --seeds 42,1337,7 --epochs 5 --chunk-char-size 800
    ... --skip-train          # 이미 학습된 산출물만 채점
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "src"))
sys.path.insert(0, str(POC / "scripts"))

SEV = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
HIGH = ("TS", "S1")

# 평가면 — 서로 다른 성격을 셋 다 본다. 한 면만 보고 일반화한 적이 있다(9/13 'S3 낙하 0' 오류).
EVAL_SETS = {
    "holdout109": "datasets/gold_real/holdout_eval.jsonl",
    "hardened42": "datasets/gold_real/holdout_eval.hardened.jsonl",
    "golden100": "datasets/gold/golden100_labeled_v2.jsonl",
}


def _norm(row: dict) -> dict | None:
    """평가셋마다 칼럼 이름이 다르다 — 정답과 본문을 못 읽으면 **버리지 말고 드러낸다.**"""
    text = row.get("text") or row.get("body") or row.get("content") or ""
    label = (
        row.get("label")
        or row.get("target")
        or row.get("gold")
        or row.get("grade")
        or row.get("intended_label")
    )
    if not text or label not in SEV:
        return None
    out = {"text": text, "label": label}
    for k in ("source", "source_type"):
        if row.get(k):
            out[k] = row[k]
    return out


def load_eval(path: Path) -> list[dict]:
    rows, bad = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        n = _norm(json.loads(line))
        if n is None:
            bad += 1
        else:
            rows.append(n)
    if bad:
        print(f"    [경고] {path.name}: 정답·본문을 못 읽은 행 {bad}건 — 분모에서 빠졌다")
    return rows


def score(model_dir: Path, rows: list[dict]) -> dict:
    from eval_p1_model_gold import predict_api_like

    preds = predict_api_like(model_dir, rows)
    hi_n = hi_miss = s3_n = s3_over = ok = 0
    ts_s1_n = ts_s1_under = 0
    miss_detail: list[str] = []
    for r, p in zip(rows, preds):
        t, pl = r["label"], p["label"]
        if pl == t:
            ok += 1
        if t == "S3":
            s3_n += 1
            if SEV.get(pl, 0) > 0:
                s3_over += 1
        else:
            hi_n += 1
            if t in HIGH:
                ts_s1_n += 1
            if SEV.get(pl, 0) < SEV[t]:
                hi_miss += 1
                if t in HIGH:
                    ts_s1_under += 1
                miss_detail.append(f"{t}->{pl}")
    return {
        "n": len(rows),
        "accuracy": round(ok / len(rows), 4) if rows else None,
        "high_n": hi_n,
        # ⚠ 이름 주의. 이것은 **과소분류**(정답보다 낮게 본 것) 건수이지
        # **무음 미탐**(과소분류 ∧ 검수로도 안 보낸 것)이 아니다. 무음 미탐은 여기서 못 센다 —
        # 검수 라우팅은 서비스 계층(status)에서 정해지고 `predict_api_like` 는 그걸 돌려주지 않는다.
        # 실측 대조(2026-09-13 golden100): 과소분류 12건 중 검수로도 안 간 것이 8건 = 무음 미탐.
        # A/B 비교에는 과소분류로 충분하다(두 조건에 같은 잣대). 보고할 때 2.78%·16% 같은
        # 무음 미탐 수치와 **나란히 놓지 말 것.**
        "underclassified": hi_miss,
        "underclassified_rate": round(hi_miss / hi_n, 4) if hi_n else None,
        # 요건이 겨냥하는 계층은 TS·S1 이다(config.high_grade_codes).
        "ts_s1_n": ts_s1_n,
        "ts_s1_under": ts_s1_under,
        "s3_n": s3_n,
        "s3_over": s3_over,
        "s3_over_rate": round(s3_over / s3_n, 4) if s3_n else None,
        "transitions": sorted(set(miss_detail)),
    }


def _resolve_model_dir(out_dir: Path) -> Path | None:
    """학습기는 `출력폴더/v-XXXXXXXX/` 아래에 모델을 남긴다 — 출력폴더 바로 밑이 아니다.

    실측 2026-09-13: `--output-dir artifacts/win_probe` 로 돌리면
    `artifacts/win_probe/v-295c496c/{config.json,model.safetensors,temperature.json}` 이 나온다.
    바로 밑만 보면 "산출물 없음"으로 조용히 건너뛴다.
    """
    if (out_dir / "config.json").exists():
        return out_dir
    cands = sorted(
        (d for d in out_dir.glob("v-*") if (d / "config.json").exists()),
        key=lambda d: d.stat().st_mtime,
    )
    return cands[-1] if cands else None


def train(out_dir: Path, seed: int, epochs: int, chunk_char_size: int | None) -> bool:
    cmd = [
        str(POC / ".venv/Scripts/python.exe"),
        "scripts/p1_train_classifier.py",
        "--mode", "full",
        "--epochs", str(epochs),
        "--seed", str(seed),
        "--no-mlflow",
        "--output-dir", str(out_dir),
    ]
    if chunk_char_size:
        cmd += ["--chunk-expand", "--chunk-char-size", str(chunk_char_size)]
    print(f"    $ {' '.join(cmd[1:])}")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=POC, capture_output=True, text=True, encoding="utf-8", errors="replace")
    dt = time.time() - t0
    if proc.returncode != 0:
        print(f"    [실패] 반환코드 {proc.returncode} · {dt/60:.1f}분")
        print("    " + "\n    ".join((proc.stderr or "").strip().splitlines()[-8:]))
        return False
    print(f"    [완료] {dt/60:.1f}분")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", default="42,1337,7")
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--chunk-char-size", type=int, default=800)
    ap.add_argument("--out-root", default="artifacts/window_ablation")
    ap.add_argument("--skip-train", action="store_true", help="학습은 건너뛰고 이미 있는 산출물만 채점")
    ap.add_argument("--report", default="reports/WINDOW_CHUNK_ABLATION.json")
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    root = POC / args.out_root
    root.mkdir(parents=True, exist_ok=True)

    evals = {}
    for name, rel in EVAL_SETS.items():
        p = POC / rel
        if not p.exists():
            print(f"[건너뜀] 평가면 없음: {rel}")
            continue
        evals[name] = load_eval(p)
        print(f"[평가면] {name}: {len(evals[name])}건")
    if not evals:
        print("평가면이 하나도 없다 — 측정을 시작하지 않는다.")
        return 2

    conditions = {
        "A_baseline": None,
        f"B_window{args.chunk_char_size}": args.chunk_char_size,
    }

    results: dict[str, dict] = {}
    for cond, ccs in conditions.items():
        results[cond] = {}
        for seed in seeds:
            out_dir = root / f"{cond}_seed{seed}"
            print(f"\n=== {cond} · seed {seed} ===")
            if not args.skip_train:
                if not train(out_dir, seed, args.epochs, ccs):
                    results[cond][str(seed)] = {"error": "train_failed"}
                    continue
            model_dir = _resolve_model_dir(out_dir)
            if model_dir is None:
                print(f"    [건너뜀] 모델 산출물 없음: {out_dir}")
                results[cond][str(seed)] = {"error": "no_model"}
                continue
            if not (model_dir / "temperature.json").exists():
                # 무보정 서빙은 고등급 무음미탐 위험이 달라져 A/B 를 오염시킨다.
                print(f"    [경고] temperature.json 없음 — 무보정 서빙: {model_dir}")
            per_set = {}
            for name, rows in evals.items():
                per_set[name] = score(model_dir, rows)
                s = per_set[name]
                print(f"    {name:12s} 과소분류 {s['underclassified']}/{s['high_n']}"
                      f" ({(s['underclassified_rate'] or 0)*100:.2f}%)"
                      f" · 그중 TS/S1 {s['ts_s1_under']}/{s['ts_s1_n']}"
                      f" · 정확도 {s['accuracy']} · S3과탐 {s['s3_over']}/{s['s3_n']}")
            results[cond][str(seed)] = per_set

    # ⚠ 과소분류만 보면 속는다 — 실측 2026-09-13: B seed1337 이 golden100 과소분류 7건으로
    # 여섯 판 중 가장 좋았는데 같은 판의 S3 과탐이 25/25 = 100% 였다. **전부 고등급이라
    # 부르면 미탐은 0 이 된다.** 그래서 정확도·과탐을 반드시 같이 낸다.
    # 범위로 낸다 — 한 판 값으로 비교하지 않는다.
    print("\n" + "=" * 72)
    print("조건별 시드 3판 범위 — 세 축을 같이 본다 (과소분류만 보면 속는다)")
    print("=" * 72)
    summary: dict[str, dict] = {}
    AXES = (
        ("underclassified", "과소분류", False),
        ("accuracy", "정확도", True),
        ("s3_over", "S3과탐", False),
    )
    b_name = f"B_window{args.chunk_char_size}"
    for name in evals:
        print(f"\n  [{name}]")
        summary[name] = {}
        for key, nice, higher_better in AXES:
            summary[name][key] = {}
            for cond in conditions:
                vals = [
                    v[name][key]
                    for v in results[cond].values()
                    if name in v and key in v[name] and v[name][key] is not None
                ]
                if not vals:
                    continue
                summary[name][key][cond] = {"values": vals, "min": min(vals), "max": max(vals)}
                print(f"    {nice:8s} {cond:18s} {vals}")
            a = summary[name][key].get("A_baseline")
            b = summary[name][key].get(b_name)
            if not (a and b):
                continue
            if b["max"] < a["min"]:
                verdict = "B 나쁨" if higher_better else "B 좋음"
            elif b["min"] > a["max"]:
                verdict = "B 좋음" if higher_better else "B 나쁨"
            else:
                verdict = "판이 겹친다 = 차이 없음"
            print(f"    {'':8s} -> {verdict}")

    out = POC / args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "seeds": seeds,
                "epochs": args.epochs,
                "chunk_char_size": args.chunk_char_size,
                "eval_sizes": {k: len(v) for k, v in evals.items()},
                "results": results,
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

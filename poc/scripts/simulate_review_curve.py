#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검수율 대 무음 미탐 곡선 — 확신도 임계(τ)와 자동확정 제외 등급으로 시뮬레이션한다.

왜: "전체 미탐율 5% 미만"을 시스템 경로(검수 포함)로 맞추려면 검수율을 얼마나 늘려야 하는지 본다.
    (2026-09-20 사용자 질문: 합성문서로 미탐율 5% 미만·재현율 90% 이상을 만족시키려면)

⚠ 이 시뮬레이션은 **확신도 임계 + 자동확정 제외 등급만** 반영한다. 합의 게이트·source_prior·metadata_floor 등 서빙의
   나머지 라우팅은 반영하지 않는다 — 그것들은 검수를 더 늘리는 방향이라 여기 나오는 무음 미탐은 **상한**이다.
   완전한 경로는 measure_serving_fnr.py(실행 중인 API 필요).

정의:  검수(needs_review) = conf < τ  또는  예측 등급 ∈ 제외 등급
       무음 하향 = 정답보다 낮게 판정했는데 검수로 안 간 것(= 사람이 못 잡은 미탐)

사용:  python scripts/simulate_review_curve.py --out reports/review_curve_recs.json     (모델을 돌려 기록을 만든다)
       python scripts/simulate_review_curve.py --recs reports/review_curve_recs.json    (저장된 기록으로 곡선만)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
RANK = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
FACES = {"golden100_v3": ("datasets/gold/golden100_labeled_v3.jsonl", "target"),
         "holdout109": ("datasets/gold_real/holdout_eval.jsonl", "label"),
         # 학습 시점 홀드아웃(학습과 같은 분포) — 정정 라벨판. 이 면은 독립 셋이 아니다.
         "v5_clean_test_fixlabels": ("datasets/labeled_p1_v5_clean_s3fix/test.jsonl", "label")}


def collect(model_dir: str) -> dict:
    os.environ.setdefault("TESTING", "1")
    sys.path.insert(0, str(POC / "src"))
    from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415

    pipe = InferencePipeline(model_dir=str(POC / model_dir))
    out = {}
    for name, (path, lk) in FACES.items():
        rows = [json.loads(line) for line in (POC / path).read_text(encoding="utf-8").splitlines() if line.strip()]
        recs = []
        for r in rows:
            res = pipe.run(r.get("text") or r.get("body"), metadata=None)
            lab = res.label.value if hasattr(res.label, "value") else str(res.label)
            recs.append({"label": r[lk], "pred": lab, "conf": float(res.confidence)})
        out[name] = recs
    return out


def sim(recs: list[dict], tau: float, excl: set[str]) -> tuple[float, float, float, int, int]:
    n = len(recs)
    review = silent_all = silent_hi = hi = 0
    for r in recs:
        is_review = r["conf"] < tau or r["pred"] in excl
        down = RANK[r["pred"]] < RANK[r["label"]]
        is_hi = r["label"] in ("TS", "S1")
        hi += is_hi
        if is_review:
            review += 1
        else:
            silent_all += down
            silent_hi += is_hi and down
    return review / n, silent_all / n, (silent_hi / hi if hi else 0.0), silent_hi, hi


def standard_recall(recs: list[dict], tau: float, excl: set[str]) -> tuple[float, dict]:
    """표준 재현율(정확 일치) — 검수된 문서는 사람이 정답으로 확정한다고 가정(상한). 반환: (4등급 평균, 등급별)."""
    ok = {g: 0 for g in RANK}
    n = {g: 0 for g in RANK}
    for r in recs:
        n[r["label"]] += 1
        if r["conf"] < tau or r["pred"] in excl or r["pred"] == r["label"]:
            ok[r["label"]] += 1
    rec = {g: ok[g] / n[g] for g in RANK if n[g]}
    return sum(rec.values()) / len(rec), rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recs", nargs="+", help="저장된 기록(JSON)으로 곡선만 계산(여러 파일이면 합친다)")
    ap.add_argument("--out", help="모델을 돌려 기록을 이 파일에 저장")
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_v5_clean/v-fe4b386b")
    a = ap.parse_args()
    if a.recs:
        data = {}
        for f in a.recs:
            data.update(json.loads(Path(f).read_text(encoding="utf-8")))
    else:
        data = collect(a.model_dir)
        if a.out:
            Path(a.out).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for name, recs in data.items():
        print(f"\n=== {name} (N={len(recs)}) ===")
        print("τ     제외등급   검수율   전체 무음하향률   고등급 무음 미탐(건/고등급)   표준재현율(4등급평균·검수=정답 가정)")
        for tau in (0.0, 0.5, 0.7, 0.8, 0.9, 0.95):
            for excl, lab in ((set(), "없음"), ({"S3"}, "S3"), ({"S2", "S3"}, "S2+S3")):
                rv, sa, sh, shn, hin = sim(recs, tau, excl)
                mac, _ = standard_recall(recs, tau, excl)
                print(f"{tau:<5} {lab:8s} {rv:7.1%}   {sa:9.1%}         {sh:6.1%} ({shn}/{hin})            {mac:6.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

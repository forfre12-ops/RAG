#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""escalation τ 스윕 — 검수 없이(서빙 경로의 확신도 승격 설정만으로) 재현율·고등급 미탐이 어떻게 움직이는지 본다.

왜: 배포 프로필(onprem-local · full-train)은 classifier_escalation_tau=0.30 을 켠다. 로컬에서 τ 를 안 주면 순수 argmax 라
    배포본보다 미탐이 큰 값이 나온다(2026-09-20 하루치 측정이 그랬다 — memory local-run-must-match-deploy-profile-flags).

규칙(pipeline.py `_select_pred_idx`): 가장 심각한 등급부터 검사해 확률 ≥ τ 인 첫 등급 채택, 없으면 argmax.
        이 도구는 τ=None 한 번의 확률 출력에서 그 규칙을 시뮬레이션한다(실제 τ=0.30 실행과 256건 중 255건 일치 확인).

선택 규칙: τ 는 **검증 분할(val)** 에서 고르고 테스트에는 1회만 적용한다. 테스트 결과를 보고 τ 를 고르면 테스트가 오염된다.

사용:  python scripts/sweep_escalation_tau.py --collect reports/escalation_scores.json     (모델을 돌려 확률 기록)
       python scripts/sweep_escalation_tau.py --scores  reports/escalation_scores.json     (스윕 표만)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
G = ["TS", "S1", "S2", "S3"]
R = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
FACES = {"synth_val": ("datasets/labeled_p1_v5_clean_s3fix/val.jsonl", "label"),
         "synth_test": ("datasets/labeled_p1_v5_clean_s3fix/test.jsonl", "label"),
         "golden100_v3": ("datasets/gold/golden100_labeled_v3.jsonl", "target"),
         "holdout109": ("datasets/gold_real/holdout_eval.jsonl", "label")}


def collect(model_dir: str) -> dict:
    os.environ.setdefault("TESTING", "1")
    sys.path.insert(0, str(POC / "src"))
    from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415

    pipe = InferencePipeline(model_dir=str(POC / model_dir))
    out = {}
    for name, (path, lk) in FACES.items():
        rows = [json.loads(x) for x in (POC / path).read_text(encoding="utf-8").splitlines() if x.strip()]
        recs = []
        for r in rows:
            res = pipe.run(r.get("text") or r.get("body"), metadata=None)
            code = res.label.value if hasattr(res.label, "value") else str(res.label)
            recs.append({"label": r[lk], "pred": code, "conf": float(res.confidence),
                         "scores": {k: float(v) for k, v in res.scores.items()}})
        out[name] = recs
    return out


def escalate(r: dict, tau: float | None) -> str:
    if tau is None:
        return r["pred"]
    for g in G:  # 심각한 것부터
        if r["scores"][g] >= tau:
            return g if R[g] > R[r["pred"]] else r["pred"]
    return r["pred"]


def metrics(recs: list[dict], tau: float | None) -> dict:
    n = {g: 0 for g in G}
    ok = {g: 0 for g in G}
    down = {g: 0 for g in G}
    up = {g: 0 for g in G}
    for r in recs:
        p, lab = escalate(r, tau), r["label"]
        n[lab] += 1
        ok[lab] += p == lab
        down[lab] += R[p] < R[lab]
        up[lab] += R[p] > R[lab]
    rec = {g: ok[g] / n[g] for g in G}
    return {"macro": sum(rec.values()) / 4, "rec": rec, "hi_miss": down["TS"] + down["S1"], "hi_n": n["TS"] + n["S1"],
            "over": up["S2"] + up["S3"], "over_n": n["S2"] + n["S3"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collect")
    ap.add_argument("--scores")
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_v5_clean/v-fe4b386b")
    a = ap.parse_args()
    if a.collect:
        data = collect(a.model_dir)
        Path(a.collect).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    elif a.scores:
        data = json.loads(Path(a.scores).read_text(encoding="utf-8"))
    else:
        ap.error("--collect 또는 --scores 가 필요하다")
    print("표준 재현율 = 4등급 평균(정확 일치) · 고등급 미탐 = TS·S1 정답을 정답보다 낮게 판정 · 과분류 = S2·S3 정답을 높게 판정")
    print("τ      | " + " | ".join(f"{n}: 재현율 · 고등급미탐" for n in FACES if n in data))
    for tau in (None, 0.5, 0.4, 0.35, 0.3, 0.25, 0.2, 0.15, 0.1):
        cells = []
        for name in FACES:
            if name not in data:
                continue
            m = metrics(data[name], tau)
            cells.append(f"{m['macro']:5.1%} · {m['hi_miss']}/{m['hi_n']}={m['hi_miss'] / m['hi_n']:4.1%}"
                         f" · 과분류 {m['over']}/{m['over_n']}")
        print(f"{str(tau):6s} | " + " | ".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())

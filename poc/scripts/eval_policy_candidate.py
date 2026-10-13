#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""정책 라벨 정정 학습 후보를 배포본·기존 조건 학습본과 같은 조건(τ=0.30)으로 평가한다.

평가 면(전부 서빙경로 pipe.run, escalation τ=0.30)
  synth_test    datasets/labeled_p1_v5_clean_policy/test.jsonl  — 학습 분포 내 보류 256건, 정책 라벨
  golden100_v3  독립 생성 합성(지름길 셋 — 절대값이 아니라 상대 비교용)
  holdout109    일부 실문서 포함 · 정정 라벨(22건)
지표: 4등급 평균 재현율 · 등급별 재현율 · 고등급(TS·S1) 미탐(정답보다 낮게 판정) · holdout109 금융보고서 S3 49건 과대분류 · 판례 S3 재현율

사용:  python scripts/eval_policy_candidate.py <out.json> 이름=모델폴더 [이름=모델폴더 ...]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
G = ["TS", "S1", "S2", "S3"]
R = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
FACES = {"synth_test": ("datasets/labeled_p1_v5_clean_policy/test.jsonl", "label"),
         "golden100_v3": ("datasets/gold/golden100_labeled_v3.jsonl", "target"),
         "holdout109": ("datasets/gold_real/holdout_eval.jsonl", "label")}


def load(rel):
    return [json.loads(x) for x in (POC / rel).read_text(encoding="utf-8").splitlines() if x.strip()]


def metrics(pairs):
    n = {g: 0 for g in G}
    ok = {g: 0 for g in G}
    dn = {g: 0 for g in G}
    for lab, p in pairs:
        n[lab] += 1
        ok[lab] += p == lab
        dn[lab] += R[p] < R[lab]
    have = [g for g in G if n[g]]
    return {"n": n, "ok": ok, "down": dn, "macro": sum(ok[g] / n[g] for g in have) / len(have),
            "hi_miss": dn["TS"] + dn["S1"], "hi_n": n["TS"] + n["S1"]}


def main() -> int:
    os.environ.setdefault("TESTING", "1")
    sys.path.insert(0, str(POC / "src"))
    from koipa.config import settings  # noqa: PLC0415
    from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415

    settings.classifier_escalation_tau = 0.30
    out_path = Path(sys.argv[1])
    res = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    faces = {name: load(rel) for name, (rel, _) in FACES.items()}
    for spec in sys.argv[2:]:
        name, path = spec.split("=", 1)
        if name in res:
            continue
        pipe = InferencePipeline(model_dir=str(POC / path) if not Path(path).is_absolute() else path)
        r = {}
        for face, rows in faces.items():
            lk = FACES[face][1]
            pred = []
            for row in rows:
                res_ = pipe.run(row.get("text") or row.get("body"), metadata=None)
                pred.append(res_.label.value if hasattr(res_.label, "value") else str(res_.label))
            r[face] = metrics([(row[lk], p) for row, p in zip(rows, pred)])
            if face == "holdout109":
                fin = [p for row, p in zip(rows, pred) if row.get("source") == "금융보고서" and row[lk] == "S3"]
                r["h109_fin_S3_n"], r["h109_fin_S3_over"] = len(fin), sum(R[p] > 0 for p in fin)
                court = [p for row, p in zip(rows, pred) if str(row.get("source") or "").startswith("판례") and row[lk] == "S3"]
                r["h109_court_S3_n"], r["h109_court_S3_ok"] = len(court), sum(p == "S3" for p in court)
        res[name] = r
        out_path.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        s, g, h = r["synth_test"], r["golden100_v3"], r["holdout109"]
        print(f"{name}: synth {s['macro']:.1%}/{s['hi_miss']}of{s['hi_n']} · golden100 {g['macro']:.1%}/{g['hi_miss']}of{g['hi_n']} · holdout109 {h['macro']:.1%}/{h['hi_miss']}of{h['hi_n']}"
              f" · 금융보고서 과대 {r['h109_fin_S3_over']}/{r['h109_fin_S3_n']}", flush=True)
    print("EVAL_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

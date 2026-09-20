#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""판결문 오라벨 정정(정책 라벨) 학습 대 기준 학습 — 5분할 교차검증 짝 비교.

두 팔은 같은 분할·같은 학습 조건이고 학습 라벨만 다르다.
  baseline  reports/phase1_cv           학습 라벨 = 원본 v5_clean
  courtfix  reports/phase1_cv_courtfix  학습 라벨 = 정책 라벨(정정본 s3fix 65건 + 현재 판결문 검출기 적중 문서 → S3)
평가는 두 팔 모두 **정책 라벨**, escalation τ=0.30.

채택 기준은 결과를 보기 전에 reports/phase1_cv_courtfix/prereg_decision.json 에 고정했다.
사용:  python scripts/compare_courtfix_cv.py
"""
from __future__ import annotations

import json
import sys
from math import sqrt
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "scripts"))
sys.path.insert(0, str(POC / "src"))
from build_p1_v5_clean import is_public_ruling  # noqa: E402
from koipa.services.synth_quality import _grade_term_pattern  # noqa: E402

G = ["TS", "S1", "S2", "S3"]
R = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
K = 5


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def load_arm(dirname: str, policy_truth: bool) -> list[dict]:
    out = []
    for k in range(K):
        d = POC / "reports" / dirname / f"fold{k}"
        preds = json.loads((d / "preds.json").read_text(encoding="utf-8"))
        rows = [json.loads(x) for x in (d / "test.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        assert len(preds) == len(rows)
        for p, r in zip(preds, rows):
            p["ruling"] = is_public_ruling(r)
            p["gradeword"] = bool(_grade_term_pattern().search(r["text"]))
            if policy_truth and p["ruling"]:
                p["label"] = "S3"
            out.append(p)
    return out


def m(recs):
    n = {g: 0 for g in G}
    ok = {g: 0 for g in G}
    dn = {g: 0 for g in G}
    for r in recs:
        n[r["label"]] += 1
        ok[r["label"]] += r["pred"] == r["label"]
        dn[r["label"]] += R[r["pred"]] < R[r["label"]]
    have = [g for g in G if n[g]]
    hi, hm = n["TS"] + n["S1"], dn["TS"] + dn["S1"]
    return {"macro": sum(ok[g] / n[g] for g in have) / len(have), "rec": {g: (ok[g] / n[g] if n[g] else None) for g in G}, "n": n,
            "hi": hi, "hm": hm, "ci": wilson(hm, hi)}


def line(name, recs):
    x = m(recs)
    rc = " ".join(f"{g} {x['rec'][g]:.0%}({x['n'][g]})" if x["rec"][g] is not None else f"{g} -" for g in G)
    hs = f"{x['hm']}/{x['hi']}={x['hm'] / x['hi']:.1%} ({x['ci'][0]:.1%}~{x['ci'][1]:.1%})" if x["hi"] else "고등급 없음"
    print(f"  {name:22s} N={len(recs):4d} 재현율 {x['macro']:5.1%} | {rc} | 고등급 미탐 {hs}")
    return x


def external(dirname: str) -> dict:
    """분할 5개 모델의 독립 셋 평가 — 모델별 (재현율, 고등급 미탐 건수, 고등급 수)."""
    res = {"golden100_v3": [], "holdout109": []}
    for k in range(K):
        d = json.loads((POC / "reports" / dirname / f"fold{k}" / "preds_external.json").read_text(encoding="utf-8"))
        for name in res:
            x = m(d[name])
            res[name].append((x["macro"], x["hm"], x["hi"], x["rec"]["S3"]))
    return res


def main() -> int:
    base = load_arm("phase1_cv", policy_truth=True)
    fix = load_arm("phase1_cv_courtfix", policy_truth=False)
    print("[교차검증 — τ=0.30 · 평가 라벨 = 정책 라벨]")
    out = {}
    for tag, recs in (("baseline(학습 라벨 원본)", base), ("courtfix(학습 라벨 정책)", fix)):
        print(tag)
        out[tag] = {"all": line("전체", recs), "syn": line("synthetic_llm", [r for r in recs if r["source"] == "synthetic_llm"]),
                    "ruling": line("판결문(검출기 적중)", [r for r in recs if r["ruling"]]),
                    "nonruling": line("비판결문", [r for r in recs if not r["ruling"]]),
                    "word": line("등급명 낱말 있음", [r for r in recs if r["gradeword"]]),
                    "noword": line("등급명 낱말 없음", [r for r in recs if not r["gradeword"]])}
    print("\n[독립 셋 — 5개 분할 모델, τ=0.30]")
    eb, ef = external("phase1_cv"), external("phase1_cv_courtfix")
    for name in ("golden100_v3", "holdout109"):
        for tag, e in (("baseline", eb), ("courtfix", ef)):
            v = e[name]
            print(f"  {name:13s} {tag:9s} 재현율 " + " ".join(f"{a:.1%}" for a, *_ in v) + f" (평균 {sum(a for a, *_ in v) / K:.1%}) | 고등급 미탐 "
                  + " ".join(f"{b}/{c}" for _, b, c, _ in v) + f" (합 {sum(b for _, b, _, _ in v)}) | S3 재현율 평균 {sum(s for *_, s in v) / K:.1%}")
    # 사전 등록 기준 판정
    A, B = out["baseline(학습 라벨 원본)"]["all"], out["courtfix(학습 라벨 정책)"]["all"]
    c1 = B["hm"] / B["hi"] <= A["hm"] / A["hi"] + 0.010
    c2 = B["rec"]["S3"] >= A["rec"]["S3"] + 0.020
    ext_b = sum(x[1] for n in eb for x in eb[n]) / K
    ext_f = sum(x[1] for n in ef for x in ef[n]) / K
    c3 = ext_f <= 1.10 * ext_b
    print("\n[사전 등록 채택 기준]")
    print(f"  ① 고등급 미탐 ≤ baseline+1.0%p : {B['hm'] / B['hi']:.2%} vs {A['hm'] / A['hi']:.2%} → {'통과' if c1 else '미달'}")
    print(f"  ② S3 재현율 ≥ baseline+2.0%p   : {B['rec']['S3']:.1%} vs {A['rec']['S3']:.1%} → {'통과' if c2 else '미달'}")
    print(f"  ③ 독립 셋 고등급 미탐 평균 건수 ≤ baseline×1.10 : {ext_f:.1f} vs {ext_b:.1f} → {'통과' if c3 else '미달'}")
    print(f"  ⇒ {'채택' if (c1 and c2 and c3) else '채택하지 않는다'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

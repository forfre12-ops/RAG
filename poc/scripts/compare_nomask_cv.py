#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""등급명 낱말 삭제 학습(B) 대 정책 라벨 학습(A) — 5분할 교차검증 짝 비교.

A = reports/phase1_cv_courtfix   정책 라벨 학습, 낱말 그대로
B = reports/phase1_cv_nomask     같은 정책 라벨·같은 분할, 학습·검증 문서의 등급명 낱말(생성기 금지어 20개)을 삭제
평가(둘 다 정책 라벨·τ=0.30):
  E1 = 보류 문서 원문        E2 = 보류 문서에서 낱말을 삭제한 본문(내용만)
채택 기준은 결과 전에 reports/phase1_cv_nomask/prereg_decision.json 에 고정했다.
사용:  python scripts/compare_nomask_cv.py
"""
from __future__ import annotations

import json
import sys
import unicodedata
from math import sqrt
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))
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


def load(dirname: str, fname: str) -> list[dict]:
    out = []
    for k in range(K):
        rows = [json.loads(x) for x in (POC / "reports" / "phase1_cv" / f"fold{k}" / "test.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        preds = json.loads((POC / "reports" / dirname / f"fold{k}" / fname).read_text(encoding="utf-8"))
        assert len(rows) == len(preds)
        for r, p in zip(rows, preds):
            p["gradeword"] = bool(_grade_term_pattern().search(unicodedata.normalize("NFKC", r["text"])))
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
            "hi": hi, "hm": hm}


def show(name, recs):
    x = m(recs)
    rc = " ".join(f"{g} {x['rec'][g]:.0%}({x['n'][g]})" if x["rec"][g] is not None else f"{g} -" for g in G)
    lo, up = wilson(x["hm"], x["hi"])
    hs = f"{x['hm']}/{x['hi']}={x['hm'] / x['hi']:.1%} ({lo:.1%}~{up:.1%})" if x["hi"] else "고등급 없음"
    print(f"  {name:24s} N={len(recs):4d} 재현율 {x['macro']:5.1%} | {rc} | 고등급 미탐 {hs}")
    return x


def ext(dirname):
    tot = 0
    per = {}
    for k in range(K):
        d = json.loads((POC / "reports" / dirname / f"fold{k}" / "preds_external.json").read_text(encoding="utf-8"))
        for name in ("golden100_v3", "holdout109"):
            x = m(d[name])
            tot += x["hm"]
            per.setdefault(name, []).append((x["macro"], x["hm"], x["hi"]))
    return tot, per


def main() -> int:
    arms = {"A(낱말 그대로 학습)": "phase1_cv_courtfix", "B(낱말 삭제 학습)": "phase1_cv_nomask"}
    res = {}
    for tag, dn in arms.items():
        e1, e2 = load(dn, "preds.json"), load(dn, "preds_masked.json")
        print(f"[{tag}]")
        res[tag] = {"e1": show("E1 원문 전체", e1), "e1w": show("E1 낱말 있는 문서", [r for r in e1 if r["gradeword"]]),
                    "e1n": show("E1 낱말 없는 문서", [r for r in e1 if not r["gradeword"]]), "e2": show("E2 낱말 삭제본 전체", e2)}
    (ta, pa), (tb, pb) = ext("phase1_cv_courtfix"), ext("phase1_cv_nomask")
    print("\n[독립 셋 — 5분할 모델, τ=0.30]")
    for name in ("golden100_v3", "holdout109"):
        for tag, per in (("A", pa), ("B", pb)):
            v = per[name]
            print(f"  {name:13s} {tag} 재현율 평균 {sum(a for a, *_ in v) / K:.1%} | 고등급 미탐 " + " ".join(f"{b}/{c}" for _, b, c in v) + f" (합 {sum(b for _, b, _ in v)})")
    A, B = res["A(낱말 그대로 학습)"], res["B(낱말 삭제 학습)"]
    c1 = B["e2"]["macro"] >= A["e2"]["macro"] + 0.05
    c2 = (B["e1n"]["rec"]["S1"] >= A["e1n"]["rec"]["S1"] and B["e1n"]["rec"]["S2"] >= A["e1n"]["rec"]["S2"]
          and B["e1n"]["hm"] <= A["e1n"]["hm"])
    c3 = B["e1w"]["macro"] >= A["e1w"]["macro"] - 0.02
    c4 = tb <= 1.10 * ta
    print("\n[사전 등록 채택 기준]")
    print(f"  ① E2(내용만) 재현율 B ≥ A+5.0%p : {B['e2']['macro']:.1%} vs {A['e2']['macro']:.1%} → {'통과' if c1 else '미달'}")
    print(f"  ② 낱말 없는 문서 S1·S2 재현율 B≥A, 고등급 미탐 B≤A : S1 {B['e1n']['rec']['S1']:.0%} vs {A['e1n']['rec']['S1']:.0%} · S2 {B['e1n']['rec']['S2']:.0%} vs {A['e1n']['rec']['S2']:.0%} · 미탐 {B['e1n']['hm']} vs {A['e1n']['hm']} → {'통과' if c2 else '미달'}")
    print(f"  ③ 낱말 있는 문서 E1 재현율 B ≥ A-2.0%p : {B['e1w']['macro']:.1%} vs {A['e1w']['macro']:.1%} → {'통과' if c3 else '미달'}")
    print(f"  ④ 독립 셋 고등급 미탐 합 B ≤ A×1.10 : {tb} vs {ta} → {'통과' if c4 else '미달'}")
    print(f"  ⇒ {'채택' if (c1 and c2 and c3 and c4) else '채택하지 않는다'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

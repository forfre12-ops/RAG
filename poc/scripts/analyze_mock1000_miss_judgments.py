#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""블라인드 판정 결과(judgments_0..2.json)를 열쇠(KEY.json)와 대조한다. PROTOCOL.md 의 분류·해석 규칙을 그대로 적용한다.

열쇠는 판정 파일 3개가 모두 있을 때만 읽는다(블라인드 유지).
사용:  python scripts/analyze_mock1000_miss_judgments.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
D = POC / "reports" / "mock1000_dev800" / "miss_analysis"
VALID = {0, 1, 2, "HOLD"}


def grade(s, v, m) -> str:
    if "HOLD" in (s, v, m):
        return "HOLD"
    p = s * v * m
    return "S3" if p == 0 else ("S2" if p in (1, 2) else ("S1" if p == 4 else "TS"))


def main() -> int:
    files = [D / f"judgments_{b}.json" for b in range(3)]
    if not all(f.exists() for f in files):
        print("판정 파일이 아직 다 없다:", [f.name for f in files if not f.exists()])
        return 1
    J = {}
    bad = []
    batch_of = {}
    for b, f in enumerate(files):
        for r in json.loads(f.read_text(encoding="utf-8")):
            J[r["item_id"]] = r
            batch_of[r["item_id"]] = b
            for ax in ("S", "V", "M"):
                if r.get(ax) not in VALID:
                    bad.append((r["item_id"], ax, r.get(ax)))
    key = json.loads((D / "KEY.json").read_text(encoding="utf-8"))          # 여기서 처음 연다
    missing = sorted(set(key) - set(J))
    L = [f"판정 항목 {len(J)}/{len(key)} · 누락 {missing} · 형식 오류 {bad}"]
    rows = []
    for iid, k in key.items():
        if iid not in J:
            continue
        j = J[iid]
        g = grade(j["S"], j["V"], j["M"])
        preds = {k["model_pred_s42"], k["model_pred_s43"], k["model_pred_s44"]}
        if g == "HOLD":
            cat = "1 판정 HOLD(본문 근거 부족)"
        elif g == k["label"]:
            cat = "2 판정=잠정 라벨"
        elif g == k["model_pred_s42"]:
            cat = "3 판정≠라벨·판정=모델 예측(라벨 의심)"
        else:
            cat = "4 판정≠라벨·판정≠모델 예측(기준 모호)"
        rows.append({"item": iid, "group": k["group"], "label": k["label"], "model": k["model_pred_s42"], "judge": g, "cat": cat, "S": j["S"], "V": j["V"], "M": j["M"],
                     "batch": batch_of[iid], "source": k["source"], "model_preds": sorted(preds)})
    from scipy.stats import fisher_exact

    def by(group: str) -> list[dict]:
        return [r for r in rows if r["group"] == group]

    miss, ctrl = by("miss"), by("control")
    L += ["", f"[분류별 건수 · 미탐군 {len(miss)} vs 통제군 {len(ctrl)}]"]
    cats = sorted({r["cat"] for r in rows})
    for c in cats:
        a, b = sum(r["cat"] == c for r in miss), sum(r["cat"] == c for r in ctrl)
        L.append(f"  {c:<38} 미탐 {a:>2} ({a / len(miss):.0%}) · 통제 {b:>2} ({b / len(ctrl):.0%})")
    hm, hc = sum(r["judge"] == "HOLD" for r in miss), sum(r["judge"] == "HOLD" for r in ctrl)
    p_hold = fisher_exact([[hm, len(miss) - hm], [hc, len(ctrl) - hc]])[1]
    L.append(f"  HOLD 비율 미탐 {hm / len(miss):.0%} vs 통제 {hc / len(ctrl):.0%} (차이 {100 * (hm / len(miss) - hc / len(ctrl)):+.0f}pt, Fisher p={p_hold:.3f})")
    nm = [r for r in miss if r["judge"] != "HOLD"]
    nc = [r for r in ctrl if r["judge"] != "HOLD"]
    dm, dc = sum(r["judge"] != r["label"] for r in nm), sum(r["judge"] != r["label"] for r in nc)
    if nm and nc:
        p_dis = fisher_exact([[dm, len(nm) - dm], [dc, len(nc) - dc]])[1]
        L.append(f"  (HOLD 제외) 판정≠잠정 라벨 비율 미탐 {dm}/{len(nm)}={dm / len(nm):.0%} vs 통제 {dc}/{len(nc)}={dc / len(nc):.0%} (Fisher p={p_dis:.3f})")
    L += ["", "[축별 HOLD 비율]"]
    for ax in ("S", "V", "M"):
        a, b = sum(r[ax] == "HOLD" for r in miss), sum(r[ax] == "HOLD" for r in ctrl)
        L.append(f"  {ax}: 미탐 {a / len(miss):.0%} · 통제 {b / len(ctrl):.0%}")
    L += ["", "[판정자(배치)별 HOLD 비율 — 판정자 간 편차]"]
    for b in range(3):
        sub = [r for r in rows if r["batch"] == b]
        L.append(f"  배치{b}: {len(sub)}건 · HOLD {sum(r['judge'] == 'HOLD' for r in sub) / len(sub):.0%} · 판정=라벨 {sum(r['judge'] == r['label'] for r in sub) / len(sub):.0%}")
    L += ["", "[미탐군 상세 · 정답→모델 예측 전이별 분류]"]
    for t, n in Counter(f"{r['label']}->{r['model']}" for r in miss).most_common():
        sub = [r for r in miss if f"{r['label']}->{r['model']}" == t]
        L.append(f"  {t}: {n}건 · " + " · ".join(f"{c.split(' ')[0]}:{sum(r['cat'] == c for r in sub)}" for c in cats))
    L += ["", "[미탐군 출처별]"]
    for s in sorted({r["source"] for r in miss}):
        sub = [r for r in miss if r["source"] == s]
        L.append(f"  {s}: {len(sub)}건 · HOLD {sum(r['judge'] == 'HOLD' for r in sub)} · 판정=라벨 {sum(r['judge'] == r['label'] for r in sub)} · 라벨 의심(3) {sum(r['cat'].startswith('3') for r in sub)} · 모호(4) {sum(r['cat'].startswith('4') for r in sub)}")
    text = "\n".join(L)
    (D / "miss_analysis_result.txt").write_text(text, encoding="utf-8")
    (D / "miss_analysis_rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

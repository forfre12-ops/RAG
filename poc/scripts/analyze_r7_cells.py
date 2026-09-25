#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""7차 사전 등록(PREREG_R7.md)의 표적 셀·독립 작성 모델 판정 — FS7 대 FS.
입력: reports/mock_final_train_20260921/preds_{FS,FS7}_s{42,43,44}_dev.json (개발·표현 154건) · r6_preds_{FS,FS7}_s*.json (6차 검증 86건)
      개발 문서의 (S,V,M) = datasets/mock_final_factfirst_20260921/internal_manifest.jsonl, 6차 = CLAUDE_DOCGEN_R6_20260921/specs_pilot.json
출력: reports/mock_final_train_20260921/r7_cells_result.txt
사용:  python scripts/eval_r6_cross_generator.py   (FS7 팔 예측을 r6_preds 로 만든 뒤)  →  python scripts/analyze_r7_cells.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
OUT = POC / "reports" / "mock_final_train_20260921"
CELLS = [(2, 0, 2), (1, 2, 1), (2, 2, 0)]
SEEDS = (42, 43, 44)


def load(name):
    f = OUT / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def main() -> int:
    man = {m["review_id"]: m for m in (json.loads(x) for x in (POC / "datasets" / "mock_final_factfirst_20260921" / "internal_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip())}
    specs6 = {s["doc_key"]: s for s in json.loads((POC / "reports" / "CLAUDE_DOCGEN_R6_20260921" / "specs_pilot.json").read_text(encoding="utf-8"))}
    fam_model6 = json.loads((POC / "reports" / "CLAUDE_DOCGEN_R6_20260921" / "WRITER_MODELS_PRIVATE.json").read_text(encoding="utf-8"))["family_model"]
    L = ["7차 판정 보조 — 표적 셀·4차 시험 가족·독립 작성 모델(6차 fable) · FS 대 FS7 (시드별 평균) · 잠정 라벨"]
    sets = {"dev154": {}, "r6": {}}
    for arm in ("FS", "FS7"):
        for s in SEEDS:
            d = load(f"preds_{arm}_s{s}_dev.json")
            r = load(f"r6_preds_{arm}_s{s}.json")
            if d:
                sets["dev154"].setdefault(arm, {})[s] = [dict(p, cell=(man[p["doc_id"]]["S"], man[p["doc_id"]]["V"], man[p["doc_id"]]["M"])) for p in d]
            if r:
                sets["r6"].setdefault(arm, {})[s] = [dict(p, cell=(specs6[p["doc_id"]]["S"], specs6[p["doc_id"]]["V"], specs6[p["doc_id"]]["M"]), writer=fam_model6[specs6[p["doc_id"]]["family_id"]]) for p in r]

    def acc(rows):
        return sum(p["pred"] == p["label"] for p in rows) / len(rows) if rows else float("nan")

    def by_seed(arm, ds, pred):
        vals = []
        for s, rows in sorted(sets[ds].get(arm, {}).items()):
            sub = [p for p in rows if pred(p)]
            if sub:
                vals.append(acc(sub))
        return vals

    def show(name, ds, pred):
        cols = []
        for arm in ("FS", "FS7"):
            v = by_seed(arm, ds, pred)
            n = len([p for p in next(iter(sets[ds].get(arm, {None: []}).values()), []) if pred(p)]) if v else 0
            cols.append(f"{arm} " + (f"{sum(v) / len(v):.1%}(시드 {len(v)}개, 범위 {100 * (max(v) - min(v)):.1f}pt)" if v else "없음"))
        L.append(f"  {name:<40} n={n:<4} " + " · ".join(cols))

    L += ["", "[표적 셀 정확도 — 개발 154건 + 6차 검증 86건(haiku 제외)]"]
    for c in CELLS:
        for ds in ("dev154", "r6"):
            show(f"셀 {c} · {ds}", ds, lambda p, c=c: p["cell"] == c and p.get("writer") != "haiku")
    L += ["", "[4차 시험 가족(경계 대조) 39건]"]
    d = sets["dev154"]
    for arm in ("FS", "FS7"):
        v = [acc([p for p in rows if p.get("round") == "R4test"]) for rows in d.get(arm, {}).values()]
        ts = [sum(1 for p in rows if p.get("round") == "R4test" and p["label"] == "TS" and p["pred"] == "S1") for rows in d.get(arm, {}).values()]
        if v:
            L.append(f"  {arm}: 정확도 시드 평균 {sum(v) / len(v):.1%} (범위 {100 * (max(v) - min(v)):.1f}pt) · TS→S1 오류 건수(시드별) {ts}")
    L += ["", "[독립 작성 모델 시험 — 6차 fable 작성 40건 (7차에 fable 없음) / opus 작성 38건은 참고용]"]
    for w in ("fable", "opus"):
        show(f"6차 {w} 작성", "r6", lambda p, w=w: p.get("writer") == w)
    L += ["", "[개발·표현 154건 등급별 하향 미탐(TS·S1 을 낮게 판정) — 시드 평균]"]
    for arm in ("FS", "FS7"):
        hm = []
        for rows in d.get(arm, {}).values():
            hi = [p for p in rows if p["label"] in ("TS", "S1")]
            rank = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
            hm.append(sum(rank[p["pred"]] < rank[p["label"]] for p in hi) / len(hi))
        if hm:
            L.append(f"  {arm}: 고등급 미탐 {sum(hm) / len(hm):.1%} (범위 {100 * (max(hm) - min(hm)):.1f}pt, 시드 {len(hm)}개)")
    text = "\n".join(L)
    (OUT / "r7_cells_result.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

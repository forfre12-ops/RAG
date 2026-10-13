#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""등급별 재현율·미탐율 보고 — 현재 기준 모델(에폭 10)과 최선 모델(MIX2: 개발 800건 + 1·2차 파일럿)을 두 평가면에서.

정의(전부 pipe.run τ=0.30 의 최종 등급 기준):
  · 재현율   = 정답이 그 등급인 문서 중 정확히 맞힌 비율
  · 하향 미탐율 = 정답이 그 등급인 문서 중 정답보다 낮은 등급으로 판정된 비율(3급은 정의상 0). 프로젝트 게이트 fnr 와 같은 방향
  · 상향 오판율 = 정답보다 높은 등급으로 판정된 비율(과탐)
  · 정밀도   = 그 등급으로 판정된 문서 중 정답인 비율
평가면 A = 옛 스타일 개발 800건(잠정 라벨, 분할 평가 문서 모음) · B = 표현이 새로운 3차 시험셋(사실 근거 문서 115건, 학습에 안 씀).
사용:  python scripts/report_per_grade.py a | b
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mock1000_cv import G, K, POC, TAU, prf  # noqa: E402

OUT = POC / "reports" / "mock1000_dev800"
RANK = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}


def table(name: str, recs: list[dict]) -> tuple[list[str], dict]:
    m = prf(recs, lambda r: r["pred"])
    rows = {}
    L = [f"  {name}  (n={len(recs)}, 정확도 {m['acc']:.1%})", "    등급   건수   재현율   하향 미탐율   상향 오판율   정밀도"]
    for g in G:
        n = m["per"][g]["n"]
        under = sum(c for p, c in m["conf"][g].items() if RANK[p] < RANK[g])
        over = sum(c for p, c in m["conf"][g].items() if RANK[p] > RANK[g])
        rows[g] = {"n": n, "recall": m["per"][g]["R"], "under": under / n, "over": over / n, "prec": m["per"][g]["P"]}
        L.append(f"    {g:<3} {n:>6}   {m['per'][g]['R']:>6.1%}   {under / n:>10.1%}   {over / n:>10.1%}   {m['per'][g]['P']:>6.1%}")
    hi = [r for r in recs if r["label"] in ("TS", "S1")]
    hu = sum(1 for r in hi if RANK[r["pred"]] < RANK[r["label"]])
    both_low = sum(1 for r in hi if r["pred"] in ("S2", "S3"))
    L.append(f"    고등급(TS+S1) 하향 미탐 {hu}/{len(hi)} = {hu / len(hi):.1%} · 그중 S2·S3 로 떨어진 것(특급+1급을 '중요문서'로 합쳐 볼 때의 미탐) {both_low}/{len(hi)} = {both_low / len(hi):.1%}")
    return L, rows


def pool(name: str) -> list[dict] | None:
    out = []
    for k in range(K):
        f = OUT / "rand" / f"fold{k}" / name
        if not f.exists():
            return None
        out += json.loads(f.read_text(encoding="utf-8"))
    return out


def mean_rows(all_rows: list[dict]) -> list[str]:
    L = ["    [시드 평균] 등급   재현율   하향 미탐율   상향 오판율"]
    for g in G:
        L.append(f"      {g:<3}   {sum(r[g]['recall'] for r in all_rows) / len(all_rows):>6.1%}   {sum(r[g]['under'] for r in all_rows) / len(all_rows):>10.1%}   {sum(r[g]['over'] for r in all_rows) / len(all_rows):>10.1%}")
    return L


def part_a() -> int:
    L = ["[A] 옛 스타일 문서 — 개발 800건(잠정 라벨), 분할 평가 문서 모음"]
    for tag, name in (("기준(에폭 10, 파일럿 없음)", "preds_R576E10_s{s}.json"), ("최선 MIX2(개발 800건 + 1·2차 파일럿)", "preds_MIX2_s{s}.json")):
        L.append(f"\n {tag}")
        rows = []
        for s in (42, 43):
            p = pool(name.format(s=s))
            t, r = table(f"시드 {s}", p)
            L += t
            rows.append(r)
        L += mean_rows(rows)
    text = "\n".join(L)
    (OUT / "per_grade_A_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def part_b() -> int:
    os.environ.setdefault("TESTING", "1")
    sys.path.insert(0, str(POC / "src"))
    from koipa.config import settings  # noqa: PLC0415
    from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415
    from eval_r2_phrase_shift import load_verified  # noqa: PLC0415

    settings.classifier_escalation_tau = TAU
    docs = load_verified(POC / "reports" / "CLAUDE_DOCGEN_R3_20260921")
    L = [f"[B] 표현이 새로운 3차 시험셋 — 사실 근거 문서 {len(docs)}건(학습에 안 씀), 분할 모델 5개의 예측을 모아 계산"]
    for tag, dirname in (("기준선(파일럿 미학습, 에폭 10)", "model_R576E10_s{s}"), ("최선 MIX2(개발 800건 + 1·2차 파일럿)", "model_MIX2_s{s}")):
        L.append(f"\n {tag}")
        rows = []
        for s in (42, 43):
            recs = []
            for k in range(K):
                root = OUT / "rand" / f"fold{k}" / dirname.format(s=s)
                pipe = InferencePipeline(model_dir=str(sorted(root.glob("v-*"))[-1]))
                for d in docs:
                    r = pipe.run(d["text"], metadata=None)
                    recs.append({"label": d["grade"], "pred": r.label.value if hasattr(r.label, "value") else str(r.label)})
            t, r = table(f"시드 {s}", recs)
            L += t
            rows.append(r)
        L += mean_rows(rows)
    text = "\n".join(L)
    (OUT / "per_grade_B_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit({"a": part_a, "b": part_b}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__) or 1)())

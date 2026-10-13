"""검수 후보(labeled_synth_v3_selfconsistent 1,000건)의 길이와 길이-등급 관계를 센다.

질문(2026-09-21): "문서가 너무 짧지 않나? 길이가 등급 단서가 되지는 않나?"
센 것: 등급별 글자수 · 게이트 통과분 글자수 · 생성 때 정한 길이 구간 · 길이만으로 등급 맞히기(라벨 섞기·최빈 등급 대조).

    poc/.venv/Scripts/python.exe -X utf8 scripts/audit_review_candidates.py
"""

from __future__ import annotations

import collections
import json
import statistics as st
import sys
from pathlib import Path

import numpy as np
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.tree import DecisionTreeClassifier

POC = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
D = POC / "datasets" / "labeled_synth_v3_selfconsistent"


def q(a: list, p: float):
    return sorted(a)[int(len(a) * p)]


def length_only_accuracy(docs: list[dict]) -> tuple[float, float, float]:
    x = np.log([[d["L"]] for d in docs])
    y = np.array([d["grade"] for d in docs])
    cv = StratifiedKFold(5, shuffle=True, random_state=0)
    model = lambda: DecisionTreeClassifier(max_depth=3, random_state=0)  # noqa: E731
    rng = np.random.default_rng(0)
    real = cross_val_score(model(), x, y, cv=cv).mean()
    shuffled = np.mean([cross_val_score(model(), x, rng.permutation(y), cv=cv).mean() for _ in range(5)])
    return real, float(shuffled), max(collections.Counter(y).values()) / len(y)


def main() -> None:
    docs = [json.loads(line) for line in (D / "all_1000_before_filter.jsonl").open(encoding="utf-8")]
    gate = {}
    for line in (D / "quality_gate_20260921.jsonl").open(encoding="utf-8"):
        g = json.loads(line)
        gate[g["doc_id"]] = g
    for d in docs:
        d["L"] = len(d["text"])
        d["st"] = gate[d["doc_id"]]["quality_status"]
    print(f"[전체 {len(docs)}건] 글자수 p10/50/90 = {q([d['L'] for d in docs], .1)}/{q([d['L'] for d in docs], .5)}/{q([d['L'] for d in docs], .9)}")
    print("[등급별 글자수 p10/중앙/p90 · 200자 미만]")
    for g in ("TS", "S1", "S2", "S3"):
        a = [d["L"] for d in docs if d["grade"] == g]
        print(f"  {g}: n={len(a)} {q(a, .1)}/{int(st.median(a))}/{q(a, .9)} · {sum(x < 200 for x in a) / len(a):.0%}")
    adm = [d for d in docs if d["st"] == "admit"]
    a = [d["L"] for d in adm]
    print(f"[게이트 통과 {len(adm)}건] 글자수 p10/25/50/75/90 = " + "/".join(str(q(a, p)) for p in (.1, .25, .5, .75, .9)))
    print("[생성 때 정한 길이 구간 × 등급]")
    ct: dict = collections.defaultdict(collections.Counter)
    for d in docs:
        ct[d["char_len_band"]][d["grade"]] += 1
    for b, c in ct.items():
        print(f"  {b}: {dict(c)} 중앙 {int(st.median([d['L'] for d in docs if d['char_len_band'] == b]))}자")
    real, shuf, maj = length_only_accuracy(docs)
    print(f"[길이만으로 등급 맞히기 5겹] 전체: 실측 {real:.1%} · 라벨 섞기 {shuf:.1%} · 최빈 등급 {maj:.1%}")
    real, shuf, maj = length_only_accuracy(adm)
    print(f"                              통과분: 실측 {real:.1%} · 라벨 섞기 {shuf:.1%} · 최빈 등급 {maj:.1%}")


if __name__ == "__main__":
    main()

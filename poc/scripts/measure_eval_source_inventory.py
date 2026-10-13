#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""평가셋 재료 목록 — 어느 자료로 **길이가 등급을 누설하지 않는** 평가셋을 만들 수 있는가.

왜(2026-09-12). 감리 지적 넷(골든셋↔학습셋 혼용 · 라벨 기준 오류 · 지름길 편향 ·
일치율 35.71% 파인튜닝 재수행)이 전부 같은 하나에 막혀 있다: **고친 것이 나아졌는지 잴
평가셋이 없다.** 홀드아웃 4종은 길이만으로 등급이 갈린다
([[holdout-length-tells-grade-2026-09-05]]).

그래서 먼저 **재료를 센다.** 어느 자료에 어느 등급이 몇 건 있고, 길이가 얼마나 갈리는가.
길이 겹침 구간이 넓어야 길이를 통제한 부분집합을 뽑을 수 있다.

재는 값 둘:
    길이 중앙값 배수     등급별 중앙값의 최대/최소. 1 에 가까울수록 좋다
    길이만으로 맞히기    글자수를 10분위 구간으로 나눠 구간별 최빈등급을 찍은 적중률.
                        전체 최빈등급만 찍은 값(기준선)과의 **차이**가 곧 길이가 흘리는 양이다

사용:
    python scripts/measure_eval_source_inventory.py
    python scripts/measure_eval_source_inventory.py --json reports/EVAL_SOURCES.json
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import statistics
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))

GRADES = ("TS", "S1", "S2", "S3")


def length_leak(rows: list[tuple[int, str]], bins: int = 10) -> dict:
    """길이 구간만 보고 최빈등급을 찍었을 때의 적중률 — 기준선과 함께 낸다."""
    rows = [(n, g) for n, g in rows if g in GRADES]
    if len(rows) < bins * 2:
        return {"n": len(rows), "note": "표본이 적어 재지 않는다"}
    rows.sort()
    size = len(rows) / bins
    hit = 0
    for i in range(bins):
        chunk = rows[int(i * size):int((i + 1) * size)]
        if chunk:
            hit += collections.Counter(g for _n, g in chunk).most_common(1)[0][1]
    counts = collections.Counter(g for _n, g in rows)
    base = counts.most_common(1)[0][1]
    med = {g: int(statistics.median([n for n, gg in rows if gg == g]))
           for g in GRADES if counts[g]}
    lo, hi = min(med.values()), max(med.values())
    return {
        "n": len(rows),
        "by_grade": dict(counts),
        "median_chars": med,
        "median_ratio": round(hi / lo, 2) if lo else None,
        "length_only_rate": round(hit / len(rows) * 100, 1),
        "baseline_rate": round(base / len(rows) * 100, 1),
        "leak_pp": round((hit - base) / len(rows) * 100, 1),
    }


def _from_jsonl(path: Path) -> list[tuple[int, str]]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            label = obj.get("label") or obj.get("intended_label") or obj.get("grade")
            text = obj.get("text") or obj.get("content") or ""
            if label in GRADES and text:
                rows.append((len(text), label))
    return rows


def _from_pool(pool: Path) -> list[tuple[int, str]]:
    import eval_on_clean_candidates as _src

    _src.ROOT = pool
    return [(len(c["text"]), c["label"]) for c in _src.load_candidates() if c.get("text")]


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="평가셋 재료 목록")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    sources: dict[str, list[tuple[int, str]]] = {}
    pool = _POC / "datasets/proxy_gold/single_document_candidates"
    if pool.is_dir():
        sources["후보 풀(합성+공개실문서)"] = _from_pool(pool)
    for rel in (
        "datasets/gold_real/holdout_eval.hardened.jsonl",
        "datasets/gold_real/holdout_eval.clean.jsonl",
        "datasets/gold_real/holdout_eval.jsonl",
        "datasets/gold_real/holdout_business.clean.jsonl",
        "datasets/labeled_p1_v6_court/holdout_balanced.jsonl",
        "datasets/labeled_p1_v5_clean/test.jsonl",
        "datasets/labeled_p1_v5_clean/train.jsonl",
        "datasets/mundane_s3/holdout.jsonl",
        "datasets/patent_proxy/holdout_eval.jsonl",
    ):
        path = _POC / rel
        if path.is_file():
            sources[rel] = _from_jsonl(path)

    out = {}
    print("%-52s %6s %7s %8s %9s" % ("자료", "건수", "길이배수", "길이적중", "누설(%p)"))
    print("-" * 92)
    for name, rows in sources.items():
        rep = length_leak(rows)
        out[name] = rep
        if "note" in rep:
            print("%-52s %6d  %s" % (name[:52], rep["n"], rep["note"]))
            continue
        print("%-52s %6d %7s %7s%% %8s" % (
            name[:52], rep["n"], rep["median_ratio"],
            rep["length_only_rate"], rep["leak_pp"]))
    print("\n※ 길이적중 = 글자수 10분위 구간만 보고 최빈등급 찍기 · 누설 = 그 값 − 기준선")
    print("※ 누설이 작을수록 길이를 통제한 평가셋을 뽑기 쉽다")

    for name, rep in out.items():
        if "by_grade" in rep:
            print("\n[%s] 등급별 %s" % (name, rep["by_grade"]))
            print("   길이 중앙값 %s" % rep["median_chars"])

    if a.json:
        target = _POC / a.json
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n기록: %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

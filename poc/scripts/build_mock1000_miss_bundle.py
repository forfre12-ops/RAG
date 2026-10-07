#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""고등급 미탐 원인 분석용 블라인드 판정 묶음을 만든다 — 개발 800건 안에서만.

대상(미탐군): 시드 42·43·44 세 번 모두 정답 고등급(TS·S1)을 정답보다 낮게 판정한 문서(pipe.run τ=0.30, 1,000건 교차검증 예측) 중 개발 800건에 속한 것.
통제군: 같은 개발 800건의 고등급 문서 중 세 시드 모두 정답을 맞힌 문서에서 (정답 등급 × 출처) 분포를 미탐군과 맞춰 무작위 추출(같은 수).
판정자에게는 문서 본문과 무작위 항목 번호만 준다(라벨·모델 예측·문서번호·출처·군 구분 없음). 열쇠(KEY.json)는 판정이 끝날 때까지 열지 않는다.
사용:  python scripts/build_mock1000_miss_bundle.py
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mock1000_cv import K, POC, WORK, load_docs  # noqa: E402

OUT = POC / "reports" / "mock1000_dev800" / "miss_analysis"
RANK = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
N_BATCH = 3


def main() -> int:
    sealed = set(json.loads((POC / "reports" / "mock1000_dev800" / "SEALED_ids.json").read_text(encoding="utf-8")))
    docs = {d["doc_id"]: d for d in load_docs()}
    P = {}
    for tag, name in ((42, "preds.json"), (43, "preds_s43.json"), (44, "preds_s44.json")):
        P[tag] = {r["doc_id"]: r for k in range(K) for r in json.loads((WORK / f"fold{k}" / name).read_text(encoding="utf-8"))}
    hi = [i for i, d in docs.items() if d["label"] in ("TS", "S1") and i not in sealed]
    miss = [i for i in hi if all(RANK[P[t][i]["pred"]] < RANK[docs[i]["label"]] for t in P)]
    ok = [i for i in hi if all(P[t][i]["pred"] == docs[i]["label"] for t in P)]

    def grp(i: str) -> tuple[str, str]:
        return docs[i]["label"], ("v8" if docs[i]["source_name"] == "v8_factor_balance_fill" else "sc")

    rng = random.Random(20260924)
    need = Counter(grp(i) for i in miss)
    ctrl = []
    short = {}
    for key, n in need.items():
        pool = sorted(i for i in ok if grp(i) == key)
        rng.shuffle(pool)
        ctrl += pool[:n]
        if len(pool) < n:
            short[str(key)] = (n, len(pool))
    items = [(i, "miss") for i in miss] + [(i, "control") for i in ctrl]
    rng.shuffle(items)
    OUT.mkdir(parents=True, exist_ok=True)
    key = {}
    batches = [[] for _ in range(N_BATCH)]
    for n, (i, g) in enumerate(items):
        item_id = f"J{n + 1:03d}"
        key[item_id] = {"doc_id": i, "group": g, "label": docs[i]["label"], "model_pred_s42": P[42][i]["pred"], "model_pred_s43": P[43][i]["pred"], "model_pred_s44": P[44][i]["pred"],
                        "source": docs[i]["source_name"]}
        batches[n % N_BATCH].append({"item_id": item_id, "text": docs[i]["text"]})
    (OUT / "KEY.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    for b, rows in enumerate(batches):
        (OUT / f"bundle_{b}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"개발 800건 고등급 {len(hi)}건 중 3시드 공통 미탐 {len(miss)}건 · 3시드 모두 정답 {len(ok)}건 · 통제군 {len(ctrl)}건 · 부족한 층 {short}")
    print("미탐군 층(정답 등급, 출처)", dict(need), "· 묶음", [len(b) for b in batches])
    return 0


if __name__ == "__main__":
    sys.exit(main())

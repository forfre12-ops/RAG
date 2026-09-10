#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""문서가 학습 분포에서 얼마나 먼지를 재고, 요소 미확정과 관계있는지 본다.

묻는 것(2026-09-10): v8 이 요소를 못 읽는 것이 "학습할 때 본 것과 달라서"인가.
그렇다면 거리로 **모델이 판단할 자격이 없는 문서**를 미리 가려낼 수 있고, 그것이
벡터DB 의 쓸 자리가 된다. 미탐을 직접 줄이는 유일한 후보다.

앞선 측정(낱말 겹침)은 아무것도 못 갈랐다 — 학습셋 어휘가 2,009종뿐이라 모든 후보가
똑같이 70% 낯설었다. 눈금이 다 붙은 자였다. 그래서 의미 거리로 다시 잰다.

임베딩 = ollama 의 bge-m3(1024차원, GPU). KURE-v1 이 검색 성능은 낫지만
(@1 +5~7pp) 이 리포의 venv 는 CPU 전용 torch 라 4,700건이 너무 느리다. 여기서 재는 것은
검색 순위가 아니라 **거리 분포**이므로 이 대체를 둔다 — 다만 수치를 인용할 때 모델을
함께 적을 것.

⚠ 문서 하나를 통째로 임베딩한다(모델 최대 길이에서 잘린다). 청크 평균이 아니다 —
  document_vectors.py 의 대표벡터와 계산법이 다르므로 그 수치와 섞어 쓰지 말 것.

사용:
    python scripts/measure_ood_vs_unknown.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()


def embed(texts: list[str], *, url: str, model: str, batch: int = 16) -> list[list[float]]:
    out: list[list[float]] = []
    t0 = time.perf_counter()
    for i in range(0, len(texts), batch):
        chunk = [t[:4000] for t in texts[i:i + batch]]
        req = urllib.request.Request(
            url, data=json.dumps({"model": model, "input": chunk}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        out.extend(d["embeddings"])
        if (i // batch) % 20 == 0:
            print("  %d/%d · %.0fs" % (i + len(chunk), len(texts), time.perf_counter() - t0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bge-m3")
    ap.add_argument("--url", default="http://localhost:11434/api/embed")
    ap.add_argument("--train", default="datasets/v8/train.jsonl")
    ap.add_argument("--sweep", default="tmp/sweep_clean_v8_caus.jsonl")
    ap.add_argument("--out", default="reports/OOD_VS_UNKNOWN_2026-09-10.json")
    a = ap.parse_args()

    import numpy as np

    from eval_on_clean_candidates import load_candidates

    tr = [json.loads(l) for l in Path(a.train).open(encoding="utf-8") if l.strip()]
    tr_txt = [(r.get("text") or "").strip() for r in tr]
    tr_txt = [t for t in tr_txt if t]
    cand = load_candidates()
    sweep = {r["doc_id"]: r for r in
             (json.loads(l) for l in Path(a.sweep).open(encoding="utf-8") if l.strip())}
    cand = [c for c in cand if c["doc_id"] in sweep and (c.get("text") or "").strip()]
    print("학습 %d건 · 후보 %d건 · 임베딩 %s" % (len(tr_txt), len(cand), a.model))

    print("[1/2] 학습셋 임베딩")
    E_tr = np.asarray(embed(tr_txt, url=a.url, model=a.model), dtype="float32")
    print("[2/2] 후보 임베딩")
    E_ca = np.asarray(embed([c["text"] for c in cand], url=a.url, model=a.model), dtype="float32")

    E_tr /= np.linalg.norm(E_tr, axis=1, keepdims=True) + 1e-9
    E_ca /= np.linalg.norm(E_ca, axis=1, keepdims=True) + 1e-9
    sim = E_ca @ E_tr.T                       # 코사인 유사도
    top1 = sim.max(axis=1)
    top10 = np.sort(sim, axis=1)[:, -10:].mean(axis=1)

    import collections
    import statistics as st
    rows = []
    for i, c in enumerate(cand):
        s = sweep[c["doc_id"]]
        nunk = sum(1 for k in ("secrecy", "value", "management")
                   if s["factors"][k] == "unknown")
        rows.append({"doc_id": c["doc_id"], "origin": s["origin"], "n_unknown": nunk,
                     "top1": float(top1[i]), "top10": float(top10[i]),
                     "v8": s["v8"], "label": s["label"]})

    print()
    print("요소 미확정 개수별 · 학습셋 최근접 유사도 (1=같음)")
    by = collections.defaultdict(list)
    for r in rows:
        by[r["n_unknown"]].append(r["top1"])
    for k in sorted(by):
        v = by[k]
        print("  미확정 %d개  n=%5d  최근접 중앙 %.4f  평균 %.4f"
              % (k, len(v), st.median(v), st.mean(v)))
    print()
    print("출처별 최근접 유사도")
    byo = collections.defaultdict(list)
    for r in rows:
        byo[r["origin"]].append(r["top1"])
    for k, v in byo.items():
        print("  %-12s n=%5d  중앙 %.4f" % (k, len(v), st.median(v)))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": a.model, "n_train": len(tr_txt),
                               "n_cand": len(rows), "rows": rows},
                              ensure_ascii=False), encoding="utf-8")
    print("\n[done] %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

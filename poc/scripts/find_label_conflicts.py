#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""아주 비슷한데 등급이 다른 문서 짝을 찾아 재검수 큐로 낸다.

왜 이것만 남았는가(2026-09-10). 벡터로 등급 품질을 올리는 방안을 전수 시험했고
(scripts/measure_vector_use_battery.py) 여덟 갈래가 전부 닫혔다 — 문서 거리로 등급 정답
예측 AUROC 0.460, 문단 단위도 0.488, 유사문서+등급 화면은 보여줄 확정 등급이 0건.
양성 신호가 나온 것은 이 하나뿐이다:

    유사도 >= 0.98 : 쌍     1 · 라벨 다른 쌍   0
    유사도 >= 0.95 : 쌍    77 · 라벨 다른 쌍   9  (11.7%)   <- 쓸 만한 문턱
    유사도 >= 0.90 : 쌍 1,155 · 라벨 다른 쌍 450  (39.0%)   <- 신호가 죽는다

0.95 를 기본값으로 두는 근거가 위 표다. 0.90 은 39%가 걸려 신호가 아니라 잡음이다.

이 도구가 하는 일과 안 하는 일:
  한다   — 사람이 볼 짝의 **목록과 순서**를 낸다.
  안 한다 — 어느 쪽이 맞는지 정하지 않는다. 등급을 고치지 않는다.
            기계가 고르면 그 기계를 나중에 시험할 때 순환이 된다.

학습셋을 먼저 보는 이유: 학습 라벨이 틀리면 모델이 직접 오염된다. 평가셋 모순은
점수를 흔들지만 모델을 바꾸지는 않는다.

주의: 임베딩은 문서를 통째로 넣는다(모델 최대길이에서 잘림). document_vectors.py 의
      청크평균 대표벡터와 계산법이 다르므로 수치를 섞어 쓰지 말 것.

사용:
    python scripts/find_label_conflicts.py --data datasets/v8/train.jsonl
    python scripts/find_label_conflicts.py --data datasets/v8/train.jsonl --threshold 0.96
"""
from __future__ import annotations

import argparse
import collections
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

GRADE_ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}


def embed(texts, *, url, model, batch=32):
    out = []
    t0 = time.perf_counter()
    for i in range(0, len(texts), batch):
        chunk = [t[:4000] for t in texts[i:i + batch]]
        req = urllib.request.Request(
            url, data=json.dumps({"model": model, "input": chunk}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            out.extend(json.loads(resp.read().decode("utf-8"))["embeddings"])
        if (i // batch) % 25 == 0:
            print("  %d/%d %.0fs" % (i + len(chunk), len(texts), time.perf_counter() - t0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/v8/train.jsonl")
    ap.add_argument("--threshold", type=float, default=0.95,
                    help="이 유사도 이상인 짝만 본다. 근거는 모듈 docstring 의 표")
    ap.add_argument("--model", default="bge-m3")
    ap.add_argument("--url", default="http://localhost:11434/api/embed")
    ap.add_argument("--cache", default="", help="임베딩 .npy 캐시 경로(있으면 읽고, 없으면 쓴다)")
    ap.add_argument("--out", default="reports/label_conflicts.jsonl")
    ap.add_argument("--max-pairs", type=int, default=500)
    a = ap.parse_args()

    import numpy as np

    rows = [json.loads(line) for line in Path(a.data).open(encoding="utf-8") if line.strip()]
    rows = [r for r in rows if (r.get("text") or "").strip() and r.get("label")]
    n = len(rows)
    dist = collections.Counter(r["label"] for r in rows)
    print("%s · %d행 · 등급 %s" % (a.data, n, dict(dist.most_common())))

    cache = Path(a.cache) if a.cache else None
    if cache and cache.is_file():
        e = np.load(cache)
        print("임베딩 캐시 사용: %s %s" % (cache, e.shape))
        if e.shape[0] != n:
            print("[error] 캐시 행수(%d)가 데이터(%d)와 다르다 — 캐시를 지우고 다시 돌릴 것"
                  % (e.shape[0], n))
            return 2
    else:
        e = np.asarray(embed([r["text"] for r in rows], url=a.url, model=a.model),
                       dtype="float32")
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.save(cache, e)
            print("임베딩 캐시 저장: %s" % cache)
    e = e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)

    sim = e @ e.T
    np.fill_diagonal(sim, -2.0)

    pairs, conflicts = 0, []
    for i in range(n):
        for j in np.nonzero(sim[i, i + 1:] >= a.threshold)[0]:
            k = i + 1 + int(j)
            pairs += 1
            if rows[i]["label"] != rows[k]["label"]:
                conflicts.append((float(sim[i][k]), i, k))
    conflicts.sort(reverse=True)

    print()
    print("문턱 %.2f — 짝 %d개 · 등급이 다른 짝 %d개 (%.1f%%)"
          % (a.threshold, pairs, len(conflicts), len(conflicts) / max(pairs, 1) * 100))
    if not conflicts:
        print("등급이 다른 짝이 없다. 문턱을 내리기 전에 왜 없는지부터 볼 것 —")
        print("이 데이터가 등급별로 따로 생성됐다면 같은 등급끼리 뭉치는 것이 당연하다.")
        return 0

    gap = collections.Counter()
    for s, i, k in conflicts:
        a_, b_ = rows[i]["label"], rows[k]["label"]
        d = abs(GRADE_ORDER.get(a_, 9) - GRADE_ORDER.get(b_, 9))
        gap[d] += 1
        pass
    print("등급 차이별 — 1칸 차이는 경계 흔들림, 2칸 이상은 라벨 오류 의심")
    for d in sorted(gap):
        print("   %d칸 차이  %4d쌍" % (d, gap[d]))

    print()
    print("가장 비슷한데 등급이 다른 짝 (상위 10)")
    for s, i, k in conflicts[:10]:
        print("   %.4f  %-3s <-> %-3s   %s | %s"
              % (s, rows[i]["label"], rows[k]["label"],
                 (rows[i].get("doc_id") or rows[i].get("id") or i),
                 (rows[k].get("doc_id") or rows[k].get("id") or k)))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for s, i, k in conflicts[:a.max_pairs]:
            fh.write(json.dumps({
                "similarity": round(s, 4),
                "grade_gap": abs(GRADE_ORDER.get(rows[i]["label"], 9)
                                 - GRADE_ORDER.get(rows[k]["label"], 9)),
                "a": {"doc_id": rows[i].get("doc_id") or rows[i].get("id"),
                      "label": rows[i]["label"],
                      "document_type": rows[i].get("document_type"),
                      "excerpt": (rows[i]["text"] or "")[:300]},
                "b": {"doc_id": rows[k].get("doc_id") or rows[k].get("id"),
                      "label": rows[k]["label"],
                      "document_type": rows[k].get("document_type"),
                      "excerpt": (rows[k]["text"] or "")[:300]},
                "decision": "",      # <- 사람이 채운다. 비어 있으면 미검수다
                "reviewer_id": "",   # <- 사람이 채운다
            }, ensure_ascii=False) + "\n")
    print()
    print("[done] %s  (%d쌍 · 유사도 높은 순)" % (out, min(len(conflicts), a.max_pairs)))
    print("다음 = 사람이 decision 과 reviewer_id 를 채운다. 이 도구는 어느 쪽이 맞는지 정하지 않는다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

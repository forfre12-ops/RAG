#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""학습셋에 사실상 같은 문서가 몇 건인지 세고, **유효 표본 수**를 낸다.

묻는 것(2026-09-10): 학습셋 3,654행이 정말 3,654건만큼의 다양성인가.

왜 의심하는가 — 같은 날 후보면 1,055건에서 최근접 이웃 유사도를 쟀더니 703건(67%)이
0.95 이상이었다. 템플릿 생성이면 그럴 수 있다. 학습셋도 같은 방식으로 만들어졌다.
그렇다면 "행 수"는 다양성을 크게 부풀린 값이고, 오늘 잰 다른 것들
(어휘 2,009종 · 문서종류 5종 · 분포 밖에서 요소 못 읽음)이 전부 한 원인의 다른 얼굴이 된다.

세는 법 = 코사인 유사도 문턱으로 이어 붙여 군집을 만든다(단일연결). 군집 하나가
'사실상 한 문서'다. 유효 표본 수 = 군집 수.

⚠ 단일연결은 사슬처럼 이어지면 큰 군집을 만든다. 그래서 문턱을 여러 개 두고 함께 낸다 —
  하나만 내면 문턱을 고른 사람이 답을 고른 것이 된다.
⚠ 문서를 통째로 임베딩한다(모델 최대길이에서 잘림).

사용:
    python scripts/measure_train_duplication.py --data datasets/v8/train.jsonl
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


def embed(texts, *, url, model, batch=16):
    out = []
    t0 = time.perf_counter()
    for i in range(0, len(texts), batch):
        chunk = [t[:4000] for t in texts[i:i + batch]]
        req = urllib.request.Request(
            url, data=json.dumps({"model": model, "input": chunk}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            out.extend(json.loads(resp.read().decode("utf-8"))["embeddings"])
        if (i // batch) % 30 == 0:
            print("  %d/%d · %.0fs" % (i + len(chunk), len(texts), time.perf_counter() - t0))
    return out


def clusters(sim, thr: float) -> list[int]:
    """단일연결 군집 — union-find."""
    n = sim.shape[0]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    import numpy as np
    for i in range(n):
        for j in np.nonzero(sim[i, i + 1:] >= thr)[0]:
            a, b = find(i), find(i + 1 + int(j))
            if a != b:
                parent[a] = b
    return [find(i) for i in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="datasets/v8/train.jsonl")
    ap.add_argument("--model", default="bge-m3")
    ap.add_argument("--url", default="http://localhost:11434/api/embed")
    ap.add_argument("--out", default="reports/TRAIN_DUPLICATION_2026-09-10.json")
    a = ap.parse_args()

    import numpy as np

    rows = [json.loads(l) for l in Path(a.data).open(encoding="utf-8") if l.strip()]
    txt = [(r.get("text") or "").strip() for r in rows]
    keep = [i for i, t in enumerate(txt) if t]
    txt = [txt[i] for i in keep]
    labels = [rows[i].get("label") for i in keep]
    n = len(txt)
    print("%s · %d행" % (a.data, n))
    print("본문이 글자 그대로 같은 것: %d건" % (n - len(set(txt))))

    E = np.asarray(embed(txt, url=a.url, model=a.model), dtype="float32")
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    sim = E @ E.T

    off = sim.copy()
    np.fill_diagonal(off, -2.0)
    top1 = off.max(axis=1)
    print("\n최근접 이웃 유사도 분포")
    for thr in (0.99, 0.98, 0.95, 0.90, 0.85):
        k = int((top1 >= thr).sum())
        print("  >= %.2f  %5d / %d  %5.1f%%" % (thr, k, n, k / n * 100))

    print("\n유효 표본 수 — 사실상 같은 문서를 한 건으로 묶으면")
    res = {}
    for thr in (0.99, 0.98, 0.95, 0.90):
        cl = clusters(sim, thr)
        cnt = collections.Counter(cl)
        big = cnt.most_common(1)[0][1]
        print("  문턱 %.2f  군집 %5d  (원본의 %5.1f%%)  가장 큰 군집 %d행"
              % (thr, len(cnt), len(cnt) / n * 100, big))
        res[str(thr)] = {"clusters": len(cnt), "largest": big}

    print("\n등급별 유효 표본 (문턱 0.95)")
    cl = clusters(sim, 0.95)
    per = collections.defaultdict(set)
    for i, c in enumerate(cl):
        per[labels[i]].add(c)
    raw = collections.Counter(labels)
    for g in sorted(per, key=lambda x: -raw[x]):
        print("  %-4s 원본 %5d행 → 유효 %4d건  (%.1f%%)"
              % (g, raw[g], len(per[g]), len(per[g]) / raw[g] * 100))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"data": a.data, "model": a.model, "n": n,
                               "exact_dup": n - len(set(txt)), "by_threshold": res},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n[done] %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

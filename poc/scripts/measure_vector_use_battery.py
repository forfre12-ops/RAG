#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""벡터 인프라를 등급 품질에 쓰는 방안들을 한 판에 시험한다 — 쓸 것과 버릴 것을 가른다.

배경(2026-09-10). 문서 단위 벡터로 세 번 쟀고 세 번 다 등급을 못 맞혔다:
    낱말 겹침으로 요소 미확정 예측     69.9~71.2% 평평
    문서 거리로 등급 정답 예측         AUROC 0.460  (대조군 '판례인가' 는 1.000)
    이웃 등급 일치                     실문서 +19.4%p (n=67)

남은 유보가 하나 있었다 — 문서를 통째로 하나의 벡터로 뭉갠 것이라, 비밀이 한두 문단에만
있는 경우 평균에 묻힌다. 그래서 문단 단위로 다시 잰다. 이것이 이 판의 A 다.
A 가 살아나면 벡터가 등급 품질에 닿는 길이 하나 열리고, 죽으면 그 길은 전부 닫힌다.

  A 문단 단위 거리가 등급 정답을 예측하는가        (문서 단위 AUROC 0.460 과 대조)
  B 커버리지 구멍 — 학습셋에 이웃이 없는 후보 비율  (무엇을 더 만들어야 하는지)
  C 라벨 모순 — 아주 비슷한데 라벨이 다른 쌍       (데이터 위생)

주의: 라벨은 기계 라벨이다(사람 확정 0건). A·C 의 '정답' 은 생성 시 의도 등급이다.
주의: 비용 때문에 표본을 쓴다. 표본 수를 결과에 함께 찍는다 — 분모 없이 인용하지 말 것.

사용:
    python scripts/measure_vector_use_battery.py
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import re
import statistics as st
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

_PARA_SPLIT = re.compile(r"\n\s*\n")


def embed(texts, *, url, model, batch=32, tag=""):
    out = []
    t0 = time.perf_counter()
    for i in range(0, len(texts), batch):
        chunk = [t[:2000] for t in texts[i:i + batch]]
        req = urllib.request.Request(
            url, data=json.dumps({"model": model, "input": chunk}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            out.extend(json.loads(resp.read().decode("utf-8"))["embeddings"])
        if (i // batch) % 25 == 0:
            print("  [%s] %d/%d %.0fs" % (tag, i + len(chunk), len(texts),
                                          time.perf_counter() - t0))
    return out


def paras(text, min_len=120, max_n=12):
    """문단으로 쪼갠다 — 빈 줄 기준, 너무 짧은 것은 버린다."""
    ps = [p.strip() for p in _PARA_SPLIT.split(text or "") if p.strip()]
    ps = [p for p in ps if len(p) >= min_len]
    return ps[:max_n]


def auroc(pos, neg):
    if not pos or not neg:
        return float("nan")
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    ranks = {}
    i = 0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        rank = (i + j + 1) / 2
        for k in range(i, j):
            ranks[k] = rank
        i = j
    s = sum(ranks[k] for k, (_v, y) in enumerate(allv) if y == 1)
    n1, n0 = len(pos), len(neg)
    return (s - n1 * (n1 + 1) / 2) / (n1 * n0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bge-m3")
    ap.add_argument("--url", default="http://localhost:11434/api/embed")
    ap.add_argument("--train", default="datasets/v8/train.jsonl")
    ap.add_argument("--sweep", default="tmp/sweep_clean_v8_caus.jsonl")
    ap.add_argument("--n-train", type=int, default=500)
    ap.add_argument("--n-cand", type=int, default=350)
    ap.add_argument("--out", default="reports/VECTOR_USE_BATTERY_2026-09-10.json")
    a = ap.parse_args()

    import numpy as np

    from eval_on_clean_candidates import load_candidates

    rng = random.Random(42)
    tr = [json.loads(line) for line in Path(a.train).open(encoding="utf-8") if line.strip()]
    tr = [r for r in tr if (r.get("text") or "").strip()]
    rng.shuffle(tr)
    tr = tr[:a.n_train]

    sweep = {}
    for line in Path(a.sweep).open(encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            sweep[r["doc_id"]] = r
    cand = [c for c in load_candidates()
            if c["doc_id"] in sweep and (c.get("text") or "").strip()]
    rng.shuffle(cand)
    cand = cand[:a.n_cand]
    print("표본 - 학습 %d건 / 후보 %d건 (무작위, seed 42)" % (len(tr), len(cand)))

    tr_par = []
    for r in tr:
        tr_par.extend(paras(r["text"]))
    ca_par, ca_owner = [], []
    for i, c in enumerate(cand):
        for p in paras(c["text"]):
            ca_par.append(p)
            ca_owner.append(i)
    print("문단 - 학습 %d개 / 후보 %d개 (문서당 %.1f / %.1f)"
          % (len(tr_par), len(ca_par),
             len(tr_par) / max(len(tr), 1), len(ca_par) / max(len(cand), 1)))
    if not tr_par or not ca_par:
        print("[error] 문단이 잡히지 않았다 - 분리 규칙을 확인할 것")
        return 2

    etp = np.asarray(embed(tr_par, url=a.url, model=a.model, tag="학습문단"), dtype="float32")
    ecp = np.asarray(embed(ca_par, url=a.url, model=a.model, tag="후보문단"), dtype="float32")
    etd = np.asarray(embed([r["text"] for r in tr], url=a.url, model=a.model, tag="학습문서"),
                     dtype="float32")
    ecd = np.asarray(embed([c["text"] for c in cand], url=a.url, model=a.model, tag="후보문서"),
                     dtype="float32")
    for e in (etp, ecp, etd, ecd):
        e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-9

    par_sim = ecp @ etp.T
    per_doc_par = {}
    for k, owner in enumerate(ca_owner):
        v = float(par_sim[k].max())
        if v > per_doc_par.get(owner, -2.0):
            per_doc_par[owner] = v
    doc_sim = (ecd @ etd.T).max(axis=1)

    ok, bad = [], []
    for i, c in enumerate(cand):
        s = sweep[c["doc_id"]]
        (ok if s["v8"] == s["label"] else bad).append(i)

    print()
    print("A. 거리가 '등급을 맞혔는가' 를 예측하는가  (0.5=동전던지기)")
    print("   맞힘 %d / 틀림 %d" % (len(ok), len(bad)))
    print("   문서 단위 최근접  AUROC %.3f"
          % auroc([float(doc_sim[i]) for i in ok], [float(doc_sim[i]) for i in bad]))
    ok2 = [i for i in ok if i in per_doc_par]
    bad2 = [i for i in bad if i in per_doc_par]
    print("   문단 단위 최근접  AUROC %.3f   (문단이 잡힌 문서 %d건)"
          % (auroc([per_doc_par[i] for i in ok2], [per_doc_par[i] for i in bad2]),
             len(per_doc_par)))

    print()
    print("B. 커버리지 구멍 - 학습셋에 가까운 이웃이 없는 후보")
    for thr in (0.90, 0.85, 0.80, 0.75):
        k = int((doc_sim < thr).sum())
        print("   문서 유사도 < %.2f : %4d / %d  %5.1f%%"
              % (thr, k, len(cand), k / len(cand) * 100))
    byo = collections.defaultdict(list)
    for i, c in enumerate(cand):
        byo[sweep[c["doc_id"]]["origin"]].append(float(doc_sim[i]))
    for k, v in byo.items():
        print("   %-12s n=%4d  최근접 중앙 %.4f" % (k, len(v), st.median(v)))

    print()
    print("C. 라벨 모순 - 후보끼리 아주 비슷한데 라벨이 다른 쌍")
    cc = ecd @ ecd.T
    np.fill_diagonal(cc, -2.0)
    cres = {}
    for thr in (0.98, 0.95, 0.90):
        pairs = []
        for i in range(len(cand)):
            for j in np.nonzero(cc[i, i + 1:] >= thr)[0]:
                pairs.append((i, i + 1 + int(j)))
        diff = [(i, j) for i, j in pairs
                if sweep[cand[i]["doc_id"]]["label"] != sweep[cand[j]["doc_id"]]["label"]]
        print("   유사도 >= %.2f : 쌍 %5d / 라벨 다른 쌍 %4d  (%.1f%%)"
              % (thr, len(pairs), len(diff), len(diff) / max(len(pairs), 1) * 100))
        cres[str(thr)] = {"pairs": len(pairs), "label_diff": len(diff)}

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": a.model, "n_train": len(tr), "n_cand": len(cand),
        "n_train_par": len(tr_par), "n_cand_par": len(ca_par),
        "label_conflict": cres,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("[done] %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

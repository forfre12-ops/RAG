#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""유사 문서가 실제로 같은 등급인지 센다 — 검수 화면에 이웃을 보여줘도 되는가.

묻는 것(2026-09-10 사용자 지적): "유사한 문서를 보여주면 오히려 잘못된 판단을 줄 수
있지 않나. 유사하다고 같은 등급은 아니지 않나."

옳은 물음이다. 문서 대표벡터는 길이·서식에 크게 지배되고, 이 코퍼스는 길이가 등급을
알려주는 것으로 이미 확인됐다. 그러면 '이웃'이 **같은 등급**이 아니라 **같은 서식**일 수
있고, 그것을 등급과 함께 보여주면 검수자를 잘못 이끈다.

그래서 재는 것 = **최근접 이웃의 등급이 나와 같은 비율**을 유사도 구간별로.
같은 등급 비율이 무작위(등급 분포에서 그냥 뽑은 값)보다 확실히 높아야 화면에 올릴 값이
있다. 유사도가 높을수록 일치율이 올라가야 하고, 안 오르면 그 화면은 해롭다.

⚠ 라벨은 기계 라벨이다(사람 확정 0건). 그러므로 이 값은 '정답끼리의 일치'가 아니라
  '같은 라벨을 받은 문서끼리 모이는가'다. 사람 확정본이 생기면 다시 잴 것.

사용:
    python scripts/measure_neighbor_grade_agreement.py
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
        if (i // batch) % 20 == 0:
            print("  %d/%d · %.0fs" % (i + len(chunk), len(texts), time.perf_counter() - t0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bge-m3")
    ap.add_argument("--url", default="http://localhost:11434/api/embed")
    ap.add_argument("--out", default="reports/NEIGHBOR_GRADE_AGREEMENT_2026-09-10.json")
    a = ap.parse_args()

    import numpy as np

    from eval_on_clean_candidates import load_candidates

    cand = [c for c in load_candidates() if (c.get("text") or "").strip()]
    labels = [c["label"] for c in cand]
    n = len(cand)
    dist = collections.Counter(labels)
    print("후보 %d건 · 등급 분포 %s" % (n, dict(dist.most_common())))
    # 무작위 기준선 — 이웃을 아무렇게나 골랐을 때 등급이 같을 확률
    base = sum((c / n) ** 2 for c in dist.values())
    print("무작위로 이웃을 골랐을 때 등급이 같을 확률(기준선) %.1f%%" % (base * 100))

    E = np.asarray(embed([c["text"] for c in cand], url=a.url, model=a.model), dtype="float32")
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    sim = E @ E.T
    np.fill_diagonal(sim, -2.0)          # 자기 자신 제외

    order = np.argsort(-sim, axis=1)
    rows = []
    for i in range(n):
        nb = order[i][:5]
        rows.append({
            "doc_id": cand[i]["doc_id"], "label": labels[i],
            "top1_sim": float(sim[i][nb[0]]), "top1_label": labels[nb[0]],
            "top5_labels": [labels[j] for j in nb],
            "len": len(cand[i]["text"] or ""),
            "nb_len": len(cand[nb[0]]["text"] or ""),
        })

    print("\n최근접 이웃의 등급이 나와 같은가 — 유사도 구간별")
    print("  유사도 구간      n     같은 등급    기준선 대비")
    bands = [(0.95, 1.01), (0.90, 0.95), (0.85, 0.90), (0.80, 0.85), (0.0, 0.80)]
    for lo, hi in bands:
        sel = [r for r in rows if lo <= r["top1_sim"] < hi]
        if not sel:
            continue
        agree = sum(1 for r in sel if r["label"] == r["top1_label"])
        print("  %.2f~%.2f  %5d   %5.1f%%      %+.1f%%p"
              % (lo, hi, len(sel), agree / len(sel) * 100, (agree / len(sel) - base) * 100))
    agree_all = sum(1 for r in rows if r["label"] == r["top1_label"])
    print("  전체       %5d   %5.1f%%      %+.1f%%p"
          % (n, agree_all / n * 100, (agree_all / n - base) * 100))

    maj = 0
    for r in rows:
        c = collections.Counter(r["top5_labels"]).most_common(1)[0][0]
        maj += (c == r["label"])
    print("\n이웃 5개 다수결이 맞을 확률 %.1f%%  (기준선 %.1f%%)" % (maj / n * 100, base * 100))

    print("\n이웃이 '같은 등급'이 아니라 '같은 길이'를 고른 것은 아닌가")
    import statistics as st
    ratio = [min(r["len"], r["nb_len"]) / max(r["len"], r["nb_len"], 1) for r in rows]
    print("  나와 이웃의 길이 비 중앙 %.3f (1=같은 길이)" % st.median(ratio))
    same = [r for r in rows if r["label"] == r["top1_label"]]
    diff = [r for r in rows if r["label"] != r["top1_label"]]
    for nm, g in (("등급 같은 짝", same), ("등급 다른 짝", diff)):
        if g:
            rr = [min(r["len"], r["nb_len"]) / max(r["len"], r["nb_len"], 1) for r in g]
            print("    %s n=%5d  길이비 중앙 %.3f  유사도 중앙 %.4f"
                  % (nm, len(g), st.median(rr), st.median([r["top1_sim"] for r in g])))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": a.model, "n": n, "baseline": base, "rows": rows},
                              ensure_ascii=False), encoding="utf-8")
    print("\n[done] %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

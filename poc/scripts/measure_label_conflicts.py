#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""거의 같은 문서인데 등급이 다른 쌍을 **전수로** 찾아 목록으로 남긴다.

왜 이 도구가 따로 필요한가(2026-09-12). `measure_vector_use_battery.py` 의 C 항목이 같은 것을
재지만 **개수만** 남긴다(0.95 문턱 · 표본 350건 · 77쌍 중 라벨 다른 쌍 9). 어느 문서인지가 없어
사람이 열어 볼 수 없고, 그래서 **그 9쌍이 진짜 라벨 오류인지 아무도 모른다.** 이 도구는
① 표본이 아니라 전수를 보고 ② 쌍 목록(문서 둘 · 유사도 · 각 라벨 · 본문 앞부분)을 파일로 남긴다.

무엇을 근거로 "모순"이라 하는가:
    벡터 유사도가 높다 = 본문이 거의 같다. 그런데 등급이 다르다면 둘 중 하나가 틀렸거나
    등급 기준이 흔들린 자리다. **자동으로 고치지 않는다** — 본문이 같아도 공개 여부·접근권한이
    다르면 등급이 정당하게 다를 수 있다. 이 도구는 사람이 볼 목록을 줄 뿐이다.

⚠ 라벨은 **사람 확정 정답이 아니다.** 후보의 생성 의도 등급(기계 라벨)이다 — 평가 정답지는
  현재 0건이다. 그래서 지금 잡히는 것은 "정답지 오류"가 아니라 **정답지를 만들 재료의 모순**이다.
⚠ 벡터는 등급을 못 맞힌다(문서·문단 모두 AUROC 0.49). 이 도구가 쓰는 능력은 '거의 같은 문서
  찾기' 하나이고 그 능력은 대조 시험에서 AUROC 1.000 으로 확인됐다.

사용:
    python scripts/measure_label_conflicts.py                    # 전수 · 문턱 0.95
    python scripts/measure_label_conflicts.py --threshold 0.90   # 문턱을 낮춰 감도 확인
    python scripts/measure_label_conflicts.py --limit 200        # 빠른 확인(무작위 표본)
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))


def embed(texts: list[str], *, url: str, model: str, batch: int = 16) -> "object":
    """ollama 임베딩 — 단위벡터로 돌려준다(내적 = 코사인 유사도)."""
    import numpy as np
    import urllib.request

    out = []
    t0 = time.time()
    for i in range(0, len(texts), batch):
        chunk = [t[:8000] for t in texts[i: i + batch]]
        req = urllib.request.Request(
            url,
            data=json.dumps({"model": model, "input": chunk}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=600) as r:
            out.extend(json.loads(r.read())["embeddings"])
        done = min(i + batch, len(texts))
        print("   임베딩 %d/%d (%.0fs)" % (done, len(texts), time.time() - t0), flush=True)
    m = np.asarray(out, dtype="float32")
    return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-12)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="라벨 모순 쌍 전수 추출")
    ap.add_argument("--model", default="bge-m3")
    ap.add_argument("--url", default="http://localhost:11434/api/embed")
    ap.add_argument("--threshold", type=float, default=0.95)
    ap.add_argument("--limit", type=int, default=0, help="0=전수. 표본을 쓰면 분모를 함께 적는다")
    ap.add_argument("--snippet", type=int, default=400, help="쌍 목록에 담을 본문 앞부분 글자 수")
    ap.add_argument("--out", default="reports/LABEL_CONFLICTS.json")
    a = ap.parse_args(argv)

    import numpy as np

    from eval_on_clean_candidates import load_candidates

    cand = [c for c in load_candidates() if (c.get("text") or "").strip()]
    if a.limit:
        random.Random(42).shuffle(cand)
        cand = cand[: a.limit]
    print("대상 후보 %d건 (limit=%s)" % (len(cand), a.limit or "전수"), flush=True)

    e = embed([c["text"] for c in cand], url=a.url, model=a.model)
    sim = e @ e.T
    np.fill_diagonal(sim, -2.0)

    ii, jj = np.nonzero(np.triu(sim) >= a.threshold)
    pairs = []
    for i, j in zip(ii.tolist(), jj.tolist()):
        a_, b_ = cand[i], cand[j]
        pairs.append({
            "similarity": round(float(sim[i, j]), 4),
            "same_label": a_["label"] == b_["label"],
            "a": {"doc_id": a_["doc_id"], "label": a_["label"],
                  "chars": len(a_["text"]), "head": a_["text"][: a.snippet]},
            "b": {"doc_id": b_["doc_id"], "label": b_["label"],
                  "chars": len(b_["text"]), "head": b_["text"][: a.snippet]},
        })
    pairs.sort(key=lambda p: (p["same_label"], -p["similarity"]))
    diff = [p for p in pairs if not p["same_label"]]

    print("\n문턱 %.2f — 쌍 %d · 라벨 다른 쌍 %d (%.1f%%)"
          % (a.threshold, len(pairs), len(diff),
             len(diff) / max(len(pairs), 1) * 100))
    by = {}
    for p in diff:
        k = " vs ".join(sorted([p["a"]["label"], p["b"]["label"]]))
        by[k] = by.get(k, 0) + 1
    for k, v in sorted(by.items(), key=lambda kv: -kv[1]):
        print("   %-12s %d쌍" % (k, v))

    out = _POC / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "model": a.model, "threshold": a.threshold,
        "n_candidates": len(cand), "sampled": bool(a.limit),
        "n_pairs": len(pairs), "n_label_diff": len(diff),
        "label_diff_pairs": diff,
        "same_label_pairs_head": [p for p in pairs if p["same_label"]][:20],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n기록: %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

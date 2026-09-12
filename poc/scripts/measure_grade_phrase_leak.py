#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""본문에 되풀이되는 문장이 등급을 알려주는가 — 흔적을 **알고 있지 않아도** 찾는다.

왜 이 도구가 있는가(2026-09-12). 골든 후보 1,055건에서 등급별 고정 문구 하나가
85.3% 를 맞히는 것을 찾았는데, 찾은 방법이 **생성기 코드를 읽고 그 문구를 알아낸 뒤
세는 것**이었다. 그 방식으로는 다음에 다른 형태의 흔적이 생기면 못 잡는다.
이 도구는 문구를 미리 알지 못해도 찾는다 — 여러 문서에 되풀이되는 문장을 모아
등급 쏠림을 재고, 쏠린 것부터 보여 준다.

⚠ 앞선 세척(`clean_candidate_answer_leak.py`)은 "## 등급 제안 사유" 절을 걷었다.
  그 세척을 통과한 판에서도 이 도구는 흔적을 찾는다 — 세척은 아는 형태만 지운다.
⚠ 제목 누출은 `measure_title_grade_leak.py` 가 잰다(본문 첫 줄 기준). 이 도구는
  **본문 안쪽 문장**을 본다. 두 도구를 합치지 않는다 — 재는 대상이 다르다.

재는 방법:
    ① 본문을 문장으로 자른다(줄바꿈·마침표 기준, 너무 짧은 것은 버린다)
    ② 여러 문서에 되풀이되는 문장만 남긴다(--min-docs)
    ③ 문장마다 등급 분포를 보고 '한 등급 비중'(purity)을 잰다
    ④ 문장을 단서로 삼아 최빈 등급을 찍었을 때의 전체 적중률을 낸다
       — 이 값이 기준선(가장 흔한 등급 비율)보다 크게 높으면 흔적이다

사용:
    python scripts/measure_grade_phrase_leak.py
    python scripts/measure_grade_phrase_leak.py --min-docs 10 --top 15
    python scripts/measure_grade_phrase_leak.py --json reports/GRADE_PHRASE_LEAK.json
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import re
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))

# ⚠ 쉼표에서도 자른다. 처음에는 마침표·줄바꿈만으로 잘랐는데, 흔적이 **문장 안에 박혀**
#   있고(예: "최초 제기된 문제는 <고정 문구>이다, …") 그 문장 전체는 주제어가 문서마다 달라
#   유일해진다 — 되풀이 문장으로 안 잡혔다. 실제로 첫 판은 쏠림 0개로 나왔다(2026-09-12).
_SPLIT = re.compile(r"[\n。.!?,，、]+")
_WS = re.compile(r"\s+")


def sentences(text: str, min_chars: int) -> set[str]:
    """문서 한 건의 문장 집합. 같은 문장이 한 문서에 여러 번 나와도 한 번으로 센다."""
    out = set()
    for raw in _SPLIT.split(text or ""):
        s = _WS.sub(" ", raw).strip(" -·*#|")
        if len(s) >= min_chars:
            out.add(s)
    return out


def audit(*, min_docs: int, min_chars: int, pool: Path | None = None) -> dict:
    import eval_on_clean_candidates as _src

    # 다른 풀도 잴 수 있어야 한다 — 생성기를 고친 뒤 **새로 뽑은 것**과 대조하려면
    # 기존 후보 풀(검수 이력이 붙어 있어 덮어쓰면 안 된다)이 아닌 곳을 가리켜야 한다.
    if pool is not None:
        _src.ROOT = pool

    cand = [c for c in _src.load_candidates() if (c.get("text") or "").strip()]
    n = len(cand)
    by_sentence: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for c in cand:
        for s in sentences(c["text"], min_chars):
            by_sentence[s][c["label"]] += 1

    grades = collections.Counter(c["label"] for c in cand)
    base = max(grades.values()) / n if n else 0.0

    rows = []
    for s, dist in by_sentence.items():
        docs = sum(dist.values())
        if docs < min_docs:
            continue
        top_grade, top_n = dist.most_common(1)[0]
        rows.append({
            "sentence": s[:120],
            "docs": docs,
            "purity": round(top_n / docs, 4),
            "top_grade": top_grade,
            "grades": dict(dist),
            "exclusive": len(dist) == 1,
        })
    rows.sort(key=lambda r: (-r["purity"], -r["docs"]))

    # 단서 하나로 등급을 찍으면 몇 %를 맞히는가 — 문서마다 **가장 쏠린 문장**을 단서로 쓴다.
    hit = covered = 0
    best = {r["sentence"]: r for r in rows}
    for c in cand:
        cand_rows = [best[s] for s in sentences(c["text"], min_chars) if s in best]
        if not cand_rows:
            continue
        covered += 1
        pick = max(cand_rows, key=lambda r: (r["purity"], r["docs"]))
        hit += (pick["top_grade"] == c["label"])

    return {
        "n_candidates": n,
        "grade_distribution": dict(grades),
        "baseline_majority": round(base, 4),
        "min_docs": min_docs,
        "min_chars": min_chars,
        "n_repeated_sentences": len(rows),
        "n_exclusive_sentences": sum(1 for r in rows if r["exclusive"]),
        "covered_docs": covered,
        "single_clue_hits": hit,
        "single_clue_accuracy": round(hit / n, 4) if n else 0.0,
        "top": rows[:50],
    }


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="되풀이 문장의 등급 쏠림 계수")
    ap.add_argument("--min-docs", type=int, default=20, help="이 수 이상 문서에 나오는 문장만 본다")
    ap.add_argument("--min-chars", type=int, default=12, help="이보다 짧은 문장은 버린다")
    ap.add_argument("--top", type=int, default=10, help="화면에 보일 상위 문장 수")
    ap.add_argument("--json", default="")
    ap.add_argument("--pool", default="", help="다른 후보 풀 디렉터리(기본: 골든 후보 풀)")
    a = ap.parse_args(argv)

    r = audit(min_docs=a.min_docs, min_chars=a.min_chars,
              pool=Path(a.pool).resolve() if a.pool else None)
    if a.pool:
        print("풀: %s" % a.pool)
    print("분모: 후보 %d건 · 등급 분포 %s" % (r["n_candidates"], r["grade_distribution"]))
    print("되풀이 문장 %d개(%d개 문서 이상) · 그중 한 등급 전용 %d개"
          % (r["n_repeated_sentences"], r["min_docs"], r["n_exclusive_sentences"]))
    print("단서 하나로 등급 찍기: %d/%d = %.1f%%  (기준선 %.1f%% — 가장 흔한 등급만 찍기)"
          % (r["single_clue_hits"], r["n_candidates"],
             r["single_clue_accuracy"] * 100, r["baseline_majority"] * 100))
    print("\n쏠린 문장 상위 %d개:" % a.top)
    for row in r["top"][:a.top]:
        print("  %5.1f%% %4d건 %-4s %s" % (row["purity"] * 100, row["docs"], row["top_grade"], row["sentence"][:80]))
    if a.json:
        out = _POC / a.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n기록: %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

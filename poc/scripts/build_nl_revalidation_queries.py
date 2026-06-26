"""NL 재검증 쿼리 빌더 — 생성된 패러프레이즈 질의를 verbatim 필터 후 gold JSONL 로.

워크플로(에이전트)가 각 문서를 읽고 만든 '자연어 재구성 질의'를 받아:
  1. 원문 본문과의 **최장 연속 일치**(verbatim overlap)를 계산
  2. THRESHOLD 이상이면 드롭(발췌 편향 제거 — 적대검증이 지적한 핵심)
  3. 생존 질의를 retrieval_gold 스키마로 datasets/gold_real/retrieval_gold_nl.jsonl 에 기록

입력: reports/nl_queries_raw.json  [{expected_doc_id, expected_grade, text}, ...]
usage: python scripts/build_nl_revalidation_queries.py [--threshold 10]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "datasets/oss_corpus"
RAW = ROOT / "reports/nl_queries_raw.json"
OUT = ROOT / "datasets/gold_real/retrieval_gold_nl.jsonl"
GRADES = ["TS", "S1", "S2", "S3"]


def body_of(doc_id: str) -> str:
    f = CORPUS_DIR / f"{doc_id}.json"
    if not f.exists():
        return ""
    try:
        d = json.load(open(f, encoding="utf-8", errors="ignore"))
    except Exception:
        return ""
    return (d.get("title", "") + " " + (d.get("body") or "")).strip()


def max_verbatim(query: str, body: str) -> int:
    """query 와 body 의 최장 연속 공통 부분문자열 길이(문자 수)."""
    if not query or not body:
        return 0
    sm = SequenceMatcher(None, query, body, autojunk=False)
    m = sm.find_longest_match(0, len(query), 0, len(body))
    return m.size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=10,
                    help="이 길이 이상 연속 일치하면 verbatim 으로 보고 드롭")
    ap.add_argument("--raw", default=str(RAW))
    cli = ap.parse_args()

    raw = json.loads(Path(cli.raw).read_text(encoding="utf-8"))
    items = raw.get("queries", raw) if isinstance(raw, dict) else raw

    kept, dropped = [], []
    seen = set()
    for q in items:
        did = q.get("expected_doc_id")
        text = (q.get("text") or "").strip()
        grade = q.get("expected_grade")
        if not did or not text or grade not in GRADES:
            dropped.append((q, "malformed"))
            continue
        if did in seen:
            dropped.append((q, "dup_doc"))
            continue
        body = body_of(did)
        if not body:
            dropped.append((q, "doc_not_found"))
            continue
        ov = max_verbatim(text, body)
        if ov >= cli.threshold:
            dropped.append((q, f"verbatim={ov}"))
            continue
        seen.add(did)
        kept.append({
            "query_id": f"nl_revalid_{len(kept) + 1:04d}",
            "text": text,
            "expected_grade": grade,
            "expected_doc_id": did,
            "relevant_doc_ids": [did],
            "max_verbatim_overlap": ov,
            "source": "nl_paraphrase_agent",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        for r in kept:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    kept_dist = Counter(r["expected_grade"] for r in kept)
    print(f"입력 {len(items)} → 생존 {len(kept)} / 드롭 {len(dropped)} (threshold={cli.threshold}자)")
    print("  생존 등급분포: " + "  ".join(f"{g}={kept_dist.get(g, 0)}" for g in GRADES))
    drop_reasons = Counter(r for _, r in dropped)
    if dropped:
        print("  드롭 사유: " + "  ".join(f"{k}={v}" for k, v in drop_reasons.most_common()))
    ovs = [r["max_verbatim_overlap"] for r in kept]
    if ovs:
        print(f"  생존 verbatim 최대일치 분포: max={max(ovs)} 평균={sum(ovs)/len(ovs):.1f}")
    print(f"\n→ {OUT}")
    print("다음: python scripts/_bench_pg_lexical_revalidation.py "
          f"--queries {OUT.relative_to(ROOT)} --tag nl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

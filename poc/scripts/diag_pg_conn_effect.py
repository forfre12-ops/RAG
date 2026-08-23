"""같은 SQL 이 호출 방식에 따라 195ms 와 1,415ms 로 갈린다 - 어느 쪽이 출하 경로인지 가른다.

왜(2026-08-16). 지금까지 나온 값이 서로 안 맞는다.

    profile_pg_retrieval  vs.search_hybrid 로       p50   195ms
    explain_pg_lexical    EXPLAIN ANALYZE 로        p50   210ms
    tune_pg_retrieval     연결 하나로 직접 execute   p50 1,415ms
    P2 게이트 리포트       p2_compare_embeddings     p50   223ms

SQL 도 파라미터도 같다. 다른 것은 **어떻게 부르느냐** 뿐이다. 그래서 셋을 같은 쿼리로
나란히 돌리고 회차별 시간을 그대로 찍는다. 회차 중간에 값이 뛰면 준비된 구문(prepared
statement)이 범용 계획(generic plan)으로 바뀐 것이고, 처음부터 다르면 연결 자체가 원인이다.

    A  연결 하나 재사용 + text() 직접 execute
    B  매 회차 새 연결 + text() 직접 execute
    C  vs.search_hybrid (출하 경로)

⚠ 이 구분이 중요한 이유: 운영에서는 SQLAlchemy 가 연결을 **풀에서 재사용**한다.
  A 가 느린 것이 사실이면 그것이 운영 실제값이고, 지금까지의 195ms 는 측정 인공물이다.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        import io as _io
        setattr(sys, _s, _io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))

SQL_HYBRID = """
WITH d AS (
    SELECT id, payload, content,
           row_number() OVER (ORDER BY embedding <=> (:q)::vector) AS rn
    FROM tb_rag_vectors
    WHERE collection = :collection AND embedding IS NOT NULL
    ORDER BY embedding <=> (:q)::vector
    LIMIT :cand
),
l AS (
    SELECT id, payload, content,
           row_number() OVER (
               ORDER BY ts_rank(tsv, to_tsquery('simple', :qt), 1) DESC
           ) AS rn
    FROM tb_rag_vectors
    WHERE collection = :collection
      AND tsv @@ to_tsquery('simple', :qt)
    LIMIT :cand
),
fused AS (
    SELECT COALESCE(d.id, l.id) AS id,
           COALESCE(d.payload, l.payload) AS payload,
           COALESCE(d.content, l.content) AS content,
           COALESCE(1.0 / (:k + d.rn), 0) + COALESCE(1.0 / (:k + l.rn), 0) AS score
    FROM d FULL OUTER JOIN l ON d.id = l.id
)
SELECT id, payload, content, score FROM fused ORDER BY score DESC LIMIT :topk
"""


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round((len(s) - 1) * p)))] if s else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="연결 방식별 지연 차이")
    ap.add_argument("--collection", default="p2_cached_pg")
    ap.add_argument("--queries-file", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--report", default="reports/PG_CONN_EFFECT.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "pg")

    from sqlalchemy import text  # noqa: PLC0415

    from koipa.adapters.embedding import build_embedder  # noqa: PLC0415
    from koipa.adapters.vectorstore.pg_store import _CAND_N, _RRF_K, PgVectorStore  # noqa: PLC0415

    vs = PgVectorStore()
    emb = build_embedder()
    col = vs._resolve_collection(args.collection)  # noqa: SLF001

    rows = [json.loads(l) for l in
            Path(args.queries_file).read_text(encoding="utf-8").splitlines() if l.strip()]
    qs = [r.get("query") or r.get("question") or r.get("text") or "" for r in rows]
    qs = [q for q in qs if q.strip()][: args.limit]
    vecs = [emb.embed([q]).vectors[0] for q in qs]
    lits = [vs._vec_lit(v) for v in vecs]  # noqa: SLF001
    qts = [" | ".join(vs._bigram(q).split()) or "__nomatch__" for q in qs]  # noqa: SLF001
    print(f"쿼리 {len(qs)}건 · 컬렉션 {args.collection} · cand={_CAND_N}\n")

    def params(i: int) -> dict:
        return {"collection": col, "qt": qts[i], "q": lits[i],
                "cand": _CAND_N, "k": _RRF_K, "topk": 5}

    out: dict = {"n": len(qs), "runs": {}}

    # A - 연결 하나 재사용
    ta: list[float] = []
    with vs._engine.connect() as c:
        for i in range(len(qs)):
            t0 = time.perf_counter()
            c.execute(text(SQL_HYBRID), params(i)).fetchall()
            ta.append((time.perf_counter() - t0) * 1000)

    # B - 매 회차 새 연결
    tb: list[float] = []
    for i in range(len(qs)):
        t0 = time.perf_counter()
        with vs._engine.connect() as c:
            c.execute(text(SQL_HYBRID), params(i)).fetchall()
        tb.append((time.perf_counter() - t0) * 1000)

    # C - 출하 경로
    tc: list[float] = []
    for i in range(len(qs)):
        t0 = time.perf_counter()
        vs.search_hybrid(args.collection, qs[i], vecs[i], top_k=5)
        tc.append((time.perf_counter() - t0) * 1000)

    print("회차별 (ms) - 중간에 뛰면 준비구문이 범용계획으로 바뀐 것이다")
    print(f"  {'#':>3s} {'A 연결재사용':>12s} {'B 새연결':>10s} {'C search_hybrid':>16s}")
    for i in range(len(qs)):
        print(f"  {i + 1:>3d} {ta[i]:>11.1f} {tb[i]:>9.1f} {tc[i]:>15.1f}")

    print("\n요약")
    for tag, label, xs in (("A", "A 연결 재사용", ta), ("B", "B 새 연결", tb),
                           ("C", "C search_hybrid(출하)", tc)):
        print(f"  {label:24s} p50 {pct(xs, 0.5):>8.1f}ms  p95 {pct(xs, 0.95):>8.1f}ms  "
              f"평균 {st.mean(xs):>8.1f}ms")
        out["runs"][tag] = {"p50": round(pct(xs, 0.5), 1), "p95": round(pct(xs, 0.95), 1),
                            "avg": round(st.mean(xs), 1), "each": [round(x, 1) for x in xs]}

    # 풀이 같은 물리 연결을 돌려주는지 확인 - B/C 가 A 와 다르다면 그 이유를 짚어야 한다
    ids = []
    for _ in range(3):
        with vs._engine.connect() as c:
            ids.append(c.execute(text("select pg_backend_pid()")).scalar())
    out["backend_pids_over_3_checkouts"] = ids
    print(f"\n  풀 체크아웃 3회의 backend pid: {ids}"
          f"  {'(같다 - 물리 연결 재사용)' if len(set(ids)) == 1 else '(다르다)'}")

    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""어휘 검색 단독이 hybrid 전체보다 느리게 측정되는 이유를 실행계획으로 찾는다.

왜(2026-08-16). `profile_pg_retrieval.py` 가 어휘 단독 p50 1,356ms · hybrid 전체 195ms
로 **부분이 전체보다 크게** 나왔다. 부분이 전체보다 클 수 없으므로 두 쿼리가 실제로는
다른 일을 하고 있다는 뜻이다.

이미 배제한 것:
  후보 수    200 -> _CAND_N(50) 으로 맞췄다. 그대로였다.
  캐시 순서  세 경로 앞에 워밍업을 넣었다. 오히려 1,061 -> 1,356ms 로 늘었다.

남은 후보는 **실행계획**이다. 그래서 EXPLAIN (ANALYZE, BUFFERS) 로 셋을 나란히 본다.

  A  프로파일러가 쓴 단독 어휘 쿼리   ORDER BY ts_rank ... LIMIT n
  B  hybrid 안의 l CTE 그대로        row_number() OVER (ORDER BY ts_rank ...) ... LIMIT n
  C  hybrid 전체                     d FULL OUTER JOIN l

A 와 B 는 같은 행을 뽑지만 문법이 다르다. A 는 top-N 정렬을 쓸 수 있고 B 는 윈도우
함수라 입력 전체를 정렬해야 한다 - 그러면 B 가 더 느려야 하는데 측정은 반대다.
계획을 봐야 안다.
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

SQL_A = """
SELECT id FROM tb_rag_vectors
WHERE collection = :collection AND tsv @@ to_tsquery('simple', :qt)
ORDER BY ts_rank(tsv, to_tsquery('simple', :qt), 1) DESC
LIMIT :cand
"""

SQL_B = """
SELECT id, payload, content,
       row_number() OVER (
           ORDER BY ts_rank(tsv, to_tsquery('simple', :qt), 1) DESC
       ) AS rn
FROM tb_rag_vectors
WHERE collection = :collection
  AND tsv @@ to_tsquery('simple', :qt)
LIMIT :cand
"""

SQL_C = """
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


def _plan_lines(plan: dict, depth: int = 0, out: list[str] | None = None) -> list[str]:
    out = [] if out is None else out
    node = plan.get("Node Type", "?")
    detail = []
    if plan.get("Relation Name"):
        detail.append(plan["Relation Name"])
    if plan.get("Index Name"):
        detail.append(plan["Index Name"])
    if plan.get("Sort Method"):
        detail.append(f"{plan['Sort Method']} {plan.get('Sort Space Used', '')}kB")
    act = plan.get("Actual Total Time")
    rows = plan.get("Actual Rows")
    loops = plan.get("Actual Loops", 1)
    reads = plan.get("Shared Read Blocks", 0)
    hits = plan.get("Shared Hit Blocks", 0)
    out.append(f"{'  ' * depth}{node:28s} {' '.join(detail)[:34]:34s} "
               f"{act if act is not None else '-':>9}ms rows={rows} loops={loops} "
               f"hit={hits} read={reads}")
    for ch in plan.get("Plans", []) or []:
        _plan_lines(ch, depth + 1, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="어휘 검색 실행계획 대조")
    ap.add_argument("--collection", default="p2_cached_pg")
    ap.add_argument("--queries-file", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--report", default="reports/PG_LEXICAL_EXPLAIN.json")
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
    queries = [r.get("query") or r.get("question") or r.get("text") or "" for r in rows]
    queries = [q for q in queries if q.strip()][: args.limit]
    print(f"쿼리 {len(queries)}건 · 컬렉션 {args.collection} · cand={_CAND_N}\n")

    res: dict = {"collection": args.collection, "cand": _CAND_N, "queries": []}
    times: dict[str, list[float]] = {"A": [], "B": [], "C": []}

    with vs._engine.connect() as c:
        for i, q in enumerate(queries):
            qt = " | ".join(vs._bigram(q).split()) or "__nomatch__"  # noqa: SLF001
            qv = vs._vec_lit(emb.embed([q]).vectors[0])  # noqa: SLF001
            base = {"collection": col, "qt": qt, "cand": _CAND_N}
            cases = (
                ("A", SQL_A, base),
                ("B", SQL_B, base),
                ("C", SQL_C, {**base, "q": qv, "k": _RRF_K, "topk": 5}),
            )
            row: dict = {"query": q[:60], "n_tokens": len(qt.split(" | "))}
            for tag, sql, params in cases:
                # 워밍업 한 번 - 계획 캐시·버퍼 조건을 맞춘다
                c.execute(text(sql), params).fetchall()
                t0 = time.perf_counter()
                plan = c.execute(text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON){sql}"),
                                 params).scalar()
                wall = (time.perf_counter() - t0) * 1000
                p = plan[0] if isinstance(plan, list) else json.loads(plan)[0]
                exec_ms = p.get("Execution Time", 0.0)
                times[tag].append(exec_ms)
                row[tag] = {"exec_ms": round(exec_ms, 1), "wall_ms": round(wall, 1),
                            "plan": _plan_lines(p["Plan"])}
                if i == 0:
                    print(f"[{tag}] 실행 {exec_ms:.1f}ms")
                    for ln in row[tag]["plan"]:
                        print("     " + ln)
                    print()
            res["queries"].append(row)

    print("실행시간 요약 (EXPLAIN ANALYZE 의 Execution Time)")
    for tag, label in (("A", "A 단독 ORDER BY+LIMIT"), ("B", "B l CTE 그대로"),
                       ("C", "C hybrid 전체")):
        xs = sorted(times[tag])
        if not xs:
            continue
        p50 = xs[len(xs) // 2]
        print(f"  {label:26s} p50 {p50:>9.1f}ms   평균 {st.mean(xs):>9.1f}ms   "
              f"({min(xs):.0f}~{max(xs):.0f})")
        res.setdefault("summary", {})[tag] = {"p50": round(p50, 1),
                                              "avg": round(st.mean(xs), 1)}
    Path(args.report).write_text(json.dumps(res, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

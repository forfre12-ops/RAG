"""PG 검색 지연을 **설정으로 줄일 수 있는지** 잰다.

왜(2026-08-16). `explain_pg_lexical.py` 로 원인을 찾았다.

    Seq Scan  tb_rag_vectors   rows=14703 / 전체 14755
    Sort      external merge 7,888kB

어휘 후보 조건이 `tsv @@ to_tsquery('simple', bigram1 | bigram2 | ...)` 라 **문서의
99.6% 가 매칭된다.** 매칭이 그렇게 넓으면 GIN 인덱스가 순차 스캔보다 비싸서 플래너가
인덱스를 안 쓴다 - 이건 버그가 아니라 OR 의미를 택한 설계의 결과다(AND 로 하면
패러프레이즈에서 0 매치가 나서 OR 로 갔다고 pg_store 주석에 근거가 있다).

그래서 스캔 자체는 없앨 수 없다. 남은 것은 **그 스캔을 싸게 하는 설정**이다.

    work_mem                          4MB   -> 정렬 7.9MB 가 디스크로 넘친다(external merge)
    max_parallel_workers_per_gather   2     -> 워커를 늘리면 스캔이 나뉜다

둘 다 세션 단위로 SET 할 수 있어 재기동 없이 잴 수 있다. shared_buffers 는 재기동이
필요해 여기서 못 잰다(별도 항목).

⚠ work_mem 은 **연결 × 정렬 노드마다** 잡히는 값이다. 크게 올리면 동시 접속에서
  메모리가 곱해진다. 이 컨테이너 상한이 2GiB 라 얼마까지 안전한지도 함께 따진다.

⚠ 첫 판(2026-08-16)은 **틀린 값을 냈다.** 연결 하나로 40회를 돌렸더니 여섯 조합이
  전부 1,415~1,457ms 로 붙어 나왔다. `diag_pg_conn_effect.py` 로 원인을 확인했다:
  같은 연결에서 11회차부터 PostgreSQL 이 준비구문을 **범용 계획(generic plan)** 으로
  바꿔 4~8배 느려진다(200ms -> 900~2,100ms). 즉 여섯 조합 모두 범용 계획을 잰 것이라
  설정 차이가 묻혔다.
  출하 경로(`search_hybrid`)는 호출마다 연결을 반납해 이 전환이 안 일어난다. 그래서
  이 스윕도 **회차마다 새 연결**로 바꿨다 - SET 은 세션 단위라 연결마다 다시 건다.
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
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(round((len(s) - 1) * p)))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PG 설정 스윕 - 지연이 줄어드는지")
    ap.add_argument("--collection", default="p2_cached_pg")
    ap.add_argument("--queries-file", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--report", default="reports/PG_RETRIEVAL_TUNE.json")
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
    prepared = [(" | ".join(vs._bigram(q).split()) or "__nomatch__",  # noqa: SLF001
                 vs._vec_lit(emb.embed([q]).vectors[0])) for q in qs]  # noqa: SLF001
    print(f"쿼리 {len(prepared)}건 · 컬렉션 {args.collection} · cand={_CAND_N}\n")

    # (work_mem, workers) 조합. 첫 줄이 현재 배포 설정이다.
    combos = [("4MB", 2), ("32MB", 2), ("64MB", 2), ("32MB", 4), ("64MB", 4), ("64MB", 0)]

    out: dict = {"collection": args.collection, "n_queries": len(prepared), "runs": []}
    print(f"  {'work_mem':>9s} {'workers':>8s}   {'p50':>8s} {'p95':>8s} {'평균':>8s}   정렬방식")
    def _set(c) -> None:
        c.execute(text(f"SET work_mem = '{wm}'"))
        c.execute(text(f"SET max_parallel_workers_per_gather = {workers}"))

    for wm, workers in combos:
        for qt, qv in prepared[:5]:          # 워밍업 - 시간은 안 잰다
            with vs._engine.connect() as c:
                _set(c)
                c.execute(text(SQL_HYBRID), {"collection": col, "qt": qt, "q": qv,
                                             "cand": _CAND_N, "k": _RRF_K,
                                             "topk": 5}).fetchall()
        ts: list[float] = []
        for qt, qv in prepared:
            p = {"collection": col, "qt": qt, "q": qv, "cand": _CAND_N,
                 "k": _RRF_K, "topk": 5}
            # 출하 경로와 같게 회차마다 새 연결. SET 은 세션 단위라 매번 다시 건다.
            with vs._engine.connect() as c:
                _set(c)
                t0 = time.perf_counter()     # SET 비용은 재지 않는다
                c.execute(text(SQL_HYBRID), p).fetchall()
                ts.append((time.perf_counter() - t0) * 1000)
        # 정렬이 아직 디스크로 넘치는지 한 번 확인
        methods: list[str] = []
        with vs._engine.connect() as c:
            _set(c)
            plan = c.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON){SQL_HYBRID}"),
                             {"collection": col, "qt": prepared[0][0], "q": prepared[0][1],
                              "cand": _CAND_N, "k": _RRF_K, "topk": 5}).scalar()
            pj = plan[0] if isinstance(plan, list) else json.loads(plan)[0]

            def _walk(n: dict) -> None:
                if n.get("Sort Method"):
                    methods.append(f"{n['Sort Method']} {n.get('Sort Space Used', 0)}kB")
                for ch in n.get("Plans", []) or []:
                    _walk(ch)
            _walk(pj["Plan"])

        p50, p95 = pct(ts, 0.5), pct(ts, 0.95)
        print(f"  {wm:>9s} {workers:>8d}   {p50:>7.1f}ms {p95:>7.1f}ms {st.mean(ts):>7.1f}ms   "
              f"{' / '.join(methods)[:44]}")
        out["runs"].append({"work_mem": wm, "workers": workers, "p50": round(p50, 1),
                            "p95": round(p95, 1), "avg": round(st.mean(ts), 1),
                            "sort_methods": methods})

    base = out["runs"][0]["p50"]
    best = min(out["runs"], key=lambda r: r["p50"])
    print(f"\n  현재 설정 p50 {base:.1f}ms   →   최선 조합 p50 {best['p50']:.1f}ms "
          f"(work_mem={best['work_mem']} workers={best['workers']})")
    print(f"  기준 200ms  →  {'통과' if best['p50'] <= 200 else '여전히 초과'}")
    out["baseline_p50"] = base
    out["best"] = best
    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ work_mem 은 연결 × 정렬 노드마다 잡힌다. 값을 올릴 때 동시 접속 수를 함께 봐야 한다.")
    print("⚠ 회차마다 새 연결로 잰다. 연결을 재사용하면 11회차부터 범용 계획으로 바뀌어")
    print("  4~8배 느려진다(diag_pg_conn_effect.py 실측) - 그러면 설정 차이가 묻힌다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

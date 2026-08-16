"""hybrid SQL 의 정렬 폭을 줄이면 빨라지는지, 그리고 **결과가 같은지** 잰다.

왜(2026-08-16). `explain_pg_lexical.py` 로 지연의 출처를 찾았다.

    Seq Scan  tb_rag_vectors  rows=14,703 / 14,755   155ms
    Sort      22,038kB (work_mem 32MB) / external merge 7,888kB (4MB)

어휘 후보 CTE 가 `id, payload, content` 를 다 들고 14,703 행을 정렬한다. 행당 1.5KB 다.
그런데 **정렬에 필요한 것은 id 와 순위뿐**이다 - payload·content 는 최종 5건에만 있으면 된다.

그래서 CTE 를 id 만 들고 정렬하도록 좁히고, payload·content 는 마지막에 기본키
(collection, id) 로 되받는다. **랭킹 계산은 한 글자도 안 바뀐다** - ts_rank 식도,
RRF 식도, 후보 수도 같다. 바뀌는 것은 정렬을 통과하는 행의 폭뿐이다.

⚠ "같은 결과" 는 주장이 아니라 확인 대상이다. 그래서 이 스크립트는 시간만 재지 않고
  **두 SQL 의 반환 id 목록이 순서까지 같은지** 매 쿼리 대조한다. 하나라도 다르면 실패다.

⚠ 연결은 회차마다 새로 연다. 재사용하면 11회차부터 범용 계획으로 바뀌어 4~8배 느려진다
  (diag_pg_conn_effect.py 실측).
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

# SQL 두 판은 koipa_sql_variants 에 모아 둔다 - 스크립트마다 복사해 두면
# 한쪽만 고쳐져 서로 다른 것을 재게 된다.
from koipa_sql_variants import SQL_LEAN, SQL_NOW  # noqa: E402


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round((len(s) - 1) * p)))] if s else 0.0


def _sort_methods(plan: dict, out: list[str] | None = None) -> list[str]:
    out = [] if out is None else out
    if plan.get("Sort Method"):
        out.append(f"{plan['Sort Method']} {plan.get('Sort Space Used', 0)}kB")
    for ch in plan.get("Plans", []) or []:
        _sort_methods(ch, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="hybrid SQL 정렬 폭 축소 효과")
    ap.add_argument("--collection", default="p2_cached_pg")
    ap.add_argument("--queries-file", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--report", default="reports/PG_LEAN_CTE.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "pg")

    from sqlalchemy import text  # noqa: PLC0415

    from koipa.adapters.embedding import build_embedder  # noqa: PLC0415
    from koipa.adapters.vectorstore.pg_store import _CAND_N, _RRF_K, PgVectorStore  # noqa: PLC0415

    vs = PgVectorStore()
    emb = build_embedder()
    col = vs._resolve_collection(args.collection)  # noqa: SLF001

    rows = [json.loads(line) for line in
            Path(args.queries_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    qs = [r.get("query") or r.get("question") or r.get("text") or "" for r in rows]
    qs = [q for q in qs if q.strip()][: args.limit]
    prep = [{"collection": col,
             "qt": " | ".join(vs._bigram(q).split()) or "__nomatch__",  # noqa: SLF001
             "q": vs._vec_lit(emb.embed([q]).vectors[0]),  # noqa: SLF001
             "cand": _CAND_N, "k": _RRF_K, "topk": args.top_k} for q in qs]
    print(f"쿼리 {len(prep)}건 · 컬렉션 {args.collection} · cand={_CAND_N} · topk={args.top_k}\n")

    def run(sql: str, p: dict) -> tuple[float, list]:
        with vs._engine.connect() as c:
            t0 = time.perf_counter()
            got = c.execute(text(sql), p).fetchall()
            ms = (time.perf_counter() - t0) * 1000
        return ms, [(r[0], round(float(r[3]), 12)) for r in got]

    for p in prep[:5]:  # 워밍업
        run(SQL_NOW, p)
        run(SQL_LEAN, p)

    t_now: list[float] = []
    t_lean: list[float] = []
    mismatch: list[dict] = []
    for i, p in enumerate(prep):
        a, ra = run(SQL_NOW, p)
        b, rb = run(SQL_LEAN, p)
        t_now.append(a)
        t_lean.append(b)
        if ra != rb:
            mismatch.append({"i": i, "query": qs[i][:50],
                             "now": [x[0] for x in ra], "lean": [x[0] for x in rb]})

    print("지연")
    for label, xs in (("현행", t_now), ("좁힌 것", t_lean)):
        print(f"  {label:8s} p50 {pct(xs, 0.5):>7.1f}ms  p95 {pct(xs, 0.95):>7.1f}ms  "
              f"평균 {st.mean(xs):>7.1f}ms")
    gain = pct(t_now, 0.5) - pct(t_lean, 0.5)
    print(f"\n  p50 차이 {gain:+.1f}ms  ({gain / max(pct(t_now, 0.5), 1e-9) * 100:+.1f}%)")

    print(f"\n결과 동일성  {len(prep) - len(mismatch)}/{len(prep)} 쿼리에서 "
          f"반환 id·점수가 순서까지 동일")
    if mismatch:
        print("  ⚠ 다른 쿼리가 있다 - 좁힌 SQL 을 채택하면 안 된다")
        for m in mismatch[:3]:
            print(f"    [{m['i']}] {m['query']}")
            print(f"        현행 {m['now']}")
            print(f"        좁힘 {m['lean']}")

    # 정렬 방식이 실제로 바뀌었는지 확인
    plans: dict = {}
    for tag, sql in (("now", SQL_NOW), ("lean", SQL_LEAN)):
        with vs._engine.connect() as c:
            pj = c.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON){sql}"), prep[0]).scalar()
            pj = pj[0] if isinstance(pj, list) else json.loads(pj)[0]
            plans[tag] = {"exec_ms": round(pj.get("Execution Time", 0), 1),
                          "sorts": _sort_methods(pj["Plan"])}
    print("\n정렬 방식")
    for tag, label in (("now", "현행  "), ("lean", "좁힌것")):
        print(f"  {label} {plans[tag]['exec_ms']:>7.1f}ms   {' / '.join(plans[tag]['sorts'])}")

    Path(args.report).write_text(json.dumps(
        {"collection": args.collection, "n": len(prep),
         "now": {"p50": round(pct(t_now, 0.5), 1), "p95": round(pct(t_now, 0.95), 1),
                 "avg": round(st.mean(t_now), 1)},
         "lean": {"p50": round(pct(t_lean, 0.5), 1), "p95": round(pct(t_lean, 0.95), 1),
                  "avg": round(st.mean(t_lean), 1)},
         "identical": len(mismatch) == 0, "mismatch": mismatch[:10],
         "plans": plans}, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    return 0 if not mismatch else 1


if __name__ == "__main__":
    raise SystemExit(main())

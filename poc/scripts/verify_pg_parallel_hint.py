"""`search_hybrid` 에 넣은 병렬 설정이 **출하 경로에서 실제로 듣는지** 확인한다.

왜(2026-08-16). `SET LOCAL` 은 트랜잭션 블록 안에서만 유효하다. 밖에서 쓰면 PostgreSQL 이
경고만 내고 **조용히 무시한다.** SQLAlchemy 가 트랜잭션을 언제 여는지에 기대는 코드라,
"설정을 넣었다" 가 "설정이 걸렸다" 를 뜻하지 않는다. 그래서 직접 확인한다.

확인하는 것 셋.

    1  같은 트랜잭션 안에서 설정이 실제로 바뀌었는지    current_setting() 으로 읽는다
    2  워커가 실제로 그만큼 뜨는지                     EXPLAIN ANALYZE 의 Workers Launched
    3  출하 경로 지연이 실제로 줄었는지                 vs.search_hybrid 를 켜고/끄고 잰다

그리고 **결과가 같은지**도 본다. 속도를 위해 답이 바뀌면 채택할 수 없다.

⚠ 지연은 회차마다 새 연결로 잰다(출하 경로와 동일). 연결을 재사용하면 11회차부터
  범용 계획으로 바뀌어 4~8배 느려진다(diag_pg_conn_effect.py 실측).
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


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round((len(s) - 1) * p)))] if s else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="병렬 설정이 출하 경로에서 듣는지 확인")
    ap.add_argument("--collection", default="p2_cached_pg")
    ap.add_argument("--queries-file", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--report", default="reports/PG_PARALLEL_HINT_VERIFY.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "pg")

    from sqlalchemy import text  # noqa: PLC0415

    from koipa.adapters.embedding import build_embedder  # noqa: PLC0415
    from koipa.adapters.vectorstore.pg_store import PgVectorStore  # noqa: PLC0415
    from koipa.config import settings  # noqa: PLC0415

    vs = PgVectorStore()
    emb = build_embedder()
    want = int(getattr(settings, "pg_search_parallel_workers", 0) or 0)
    print(f"설정값 pg_search_parallel_workers = {want}\n")
    if want <= 0:
        print("  0 이라 아무것도 안 건다 - 확인할 것이 없다.")
        return 1

    out: dict = {"setting": want, "checks": {}}

    # --- 1) 트랜잭션 안에서 값이 실제로 바뀌는가 -------------------------------
    with vs._engine.connect() as c:  # noqa: SLF001
        before = {k: c.execute(text(f"select current_setting('{k}')")).scalar()
                  for k in ("min_parallel_table_scan_size", "max_parallel_workers_per_gather")}
        vs._apply_parallel_hint(c)  # noqa: SLF001
        after = {k: c.execute(text(f"select current_setting('{k}')")).scalar()
                 for k in ("min_parallel_table_scan_size", "max_parallel_workers_per_gather")}
    applied = (after["max_parallel_workers_per_gather"] == str(want)
               and after["min_parallel_table_scan_size"] in ("0", "0kB"))
    print("1) 트랜잭션 안에서 설정이 바뀌는가")
    for k in before:
        print(f"   {k:34s} {before[k]:>8s}  ->  {after[k]:>8s}")
    print(f"   {'적용됨' if applied else '⚠ 안 걸렸다 - SET LOCAL 이 무시됐다'}\n")
    out["checks"]["applied"] = {"before": before, "after": after, "ok": applied}

    # --- 2) 반납 후 원래대로 돌아오는가 (다른 쿼리에 새면 안 된다) --------------
    with vs._engine.connect() as c:  # noqa: SLF001
        leaked = {k: c.execute(text(f"select current_setting('{k}')")).scalar()
                  for k in ("min_parallel_table_scan_size", "max_parallel_workers_per_gather")}
    no_leak = leaked == before
    print("2) 연결을 반납한 뒤 원래 값으로 돌아오는가 (다른 쿼리로 새면 안 된다)")
    for k in leaked:
        print(f"   {k:34s} {leaked[k]:>8s}   {'같다' if leaked[k] == before[k] else '⚠ 남았다'}")
    print(f"   {'새지 않는다' if no_leak else '⚠ 설정이 남았다 - SET LOCAL 이 아니다'}\n")
    out["checks"]["no_leak"] = {"after_return": leaked, "ok": no_leak}

    # --- 3) 워커가 실제로 그만큼 뜨는가 ---------------------------------------
    rows = [json.loads(line) for line in
            Path(args.queries_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    qs = [r.get("query") or r.get("question") or r.get("text") or "" for r in rows]
    qs = [q for q in qs if q.strip()][: args.limit]
    vecs = [emb.embed([q]).vectors[0] for q in qs]

    from koipa_sql_variants import SQL_NOW  # noqa: PLC0415
    from koipa.adapters.vectorstore.pg_store import _CAND_N, _RRF_K  # noqa: PLC0415

    p0 = {"collection": vs._resolve_collection(args.collection),  # noqa: SLF001
          "qt": " | ".join(vs._bigram(qs[0]).split()) or "__nomatch__",  # noqa: SLF001
          "q": vs._vec_lit(vecs[0]), "cand": _CAND_N, "k": _RRF_K,  # noqa: SLF001
          "topk": args.top_k}

    def workers_for(hint: bool) -> int:
        with vs._engine.connect() as c:  # noqa: SLF001
            if hint:
                vs._apply_parallel_hint(c)  # noqa: SLF001
            pj = c.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON){SQL_NOW}"), p0).scalar()
            pj = pj[0] if isinstance(pj, list) else json.loads(pj)[0]

        def _w(n: dict) -> int:
            m = n.get("Workers Launched", 0) or 0
            for ch in n.get("Plans", []) or []:
                m = max(m, _w(ch))
            return m
        return _w(pj["Plan"])

    w_off, w_on = workers_for(False), workers_for(True)
    print("3) 워커가 실제로 그만큼 뜨는가")
    print(f"   설정 없이 {w_off}개  ->  설정 걸고 {w_on}개   "
          f"{'기대대로' if w_on == want else '⚠ 기대(' + str(want) + ')와 다르다'}\n")
    out["checks"]["workers"] = {"off": w_off, "on": w_on, "want": want,
                                "ok": w_on == want}

    # --- 4) 출하 경로 지연·결과 ------------------------------------------------
    def bench(enabled: int) -> tuple[list[float], list]:
        settings.pg_search_parallel_workers = enabled
        for q, v in list(zip(qs, vecs))[:5]:
            vs.search_hybrid(args.collection, q, v, top_k=args.top_k)
        ts, res = [], []
        for q, v in zip(qs, vecs):
            t0 = time.perf_counter()
            hits = vs.search_hybrid(args.collection, q, v, top_k=args.top_k)
            ts.append((time.perf_counter() - t0) * 1000)
            res.append([(h.id, round(float(h.score), 12)) for h in hits])
        return ts, res

    t_off, r_off = bench(0)
    t_on, r_on = bench(want)
    settings.pg_search_parallel_workers = want
    same = sum(1 for a, b in zip(r_off, r_on) if a == b)

    print("4) 출하 경로(search_hybrid) 지연")
    print(f"   설정 없이  p50 {pct(t_off, 0.5):>7.1f}ms  p95 {pct(t_off, 0.95):>7.1f}ms  "
          f"평균 {st.mean(t_off):>7.1f}ms")
    print(f"   설정 걸고  p50 {pct(t_on, 0.5):>7.1f}ms  p95 {pct(t_on, 0.95):>7.1f}ms  "
          f"평균 {st.mean(t_on):>7.1f}ms")
    print(f"   차이       p50 {pct(t_on, 0.5) - pct(t_off, 0.5):+7.1f}ms")
    print(f"   결과 동일  {same}/{len(qs)} 쿼리에서 id·점수가 순서까지 같다")
    out["latency"] = {"off": {"p50": round(pct(t_off, 0.5), 1),
                              "p95": round(pct(t_off, 0.95), 1)},
                      "on": {"p50": round(pct(t_on, 0.5), 1),
                             "p95": round(pct(t_on, 0.95), 1)},
                      "identical": f"{same}/{len(qs)}", "n": len(qs)}

    ok = applied and no_leak and w_on == want and same == len(qs) \
        and pct(t_on, 0.5) < pct(t_off, 0.5)
    print(f"\n종합: {'통과 - 설정이 걸리고, 새지 않고, 빨라지고, 답이 같다' if ok else '⚠ 확인 필요'}")
    out["ok"] = ok
    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"[report] {args.report}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

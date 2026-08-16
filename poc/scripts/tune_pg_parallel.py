"""hybrid 지연의 실제 레버를 찾는다 - SQL 폭 × 병렬 워커 × 병렬 강제.

왜(2026-08-16). 여기까지 실측으로 좁혀진 그림.

    지연의 출처   어휘 후보가 tb_rag_vectors 를 **순차 스캔**한다.
                  bigram 을 OR 로 묶은 질의라 14,703/14,755 행(99.6%)이 매칭돼
                  GIN 인덱스가 순차 스캔보다 비싸다 - 플래너가 인덱스를 안 쓴다.
    지배 요인     정렬이 아니라 **그 스캔의 병렬화**다.
                    단일 스레드 Seq Scan  410ms
                    2워커+리더 Seq Scan   155ms
    역설          비용이 싸 보이면 플래너가 병렬을 포기해 되레 느려진다.
                    work_mem 4MB(외부병합·병렬)   193ms
                    work_mem 32MB(퀵정렬·단일)    450ms
                    CTE 를 id 만으로 좁힘(단일)    438ms

그리고 서버 실측.

    힙 26MB · 전체 347MB          content 가 TOAST 에 있다
    쿼리 하나가 읽는 블록 814MB    14,703 행의 content 를 전부 꺼내온다
    max_parallel_workers 8         그런데 per_gather 는 2 로 묶여 있다
    호스트 CPU 8 · 컨테이너 CPU 무제한

그래서 두 가지를 **동시에** 건다.

    폭     CTE 가 id 와 순위만 들면 14,703 행의 TOAST 를 안 꺼낸다
    병렬   parallel_setup_cost·min_parallel_table_scan_size 를 낮춰 병렬을 강제하고
           per_gather 를 늘린다

⚠ 랭킹은 안 바뀌어야 한다. 그건 주장이 아니라 확인 대상이라 조합마다 현행 SQL 과
  반환 id·점수를 순서까지 대조한다. 다르면 그 조합은 채택 불가로 표시한다.

⚠ 연결은 회차마다 새로 연다. 재사용하면 11회차부터 범용 계획으로 바뀌어 4~8배
  느려진다(diag_pg_conn_effect.py 실측).

⚠ 여기서 SET 으로 잰 값은 **세션 설정**이다. 채택하려면 postgresql.conf 나 접속 시
  옵션으로 고정해야 하고, 그건 별개 작업이다.
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

from koipa_sql_variants import SQL_LEAN, SQL_NOW  # noqa: E402


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round((len(s) - 1) * p)))] if s else 0.0


def _scan(plan: dict, acc: dict | None = None) -> dict:
    acc = {"sorts": [], "hit": 0, "read": 0, "workers": 0, "seq": 0} if acc is None else acc
    if plan.get("Sort Method"):
        acc["sorts"].append(f"{plan['Sort Method']} {plan.get('Sort Space Used', 0)}kB")
    acc["hit"] += plan.get("Shared Hit Blocks", 0) or 0
    acc["read"] += plan.get("Shared Read Blocks", 0) or 0
    acc["workers"] = max(acc["workers"], plan.get("Workers Launched", 0) or 0)
    if plan.get("Node Type") == "Seq Scan":
        acc["seq"] += 1
    for ch in plan.get("Plans", []) or []:
        _scan(ch, acc)
    return acc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="hybrid 지연 - SQL 폭 × 병렬 조합")
    ap.add_argument("--collection", default="p2_cached_pg")
    ap.add_argument("--queries-file", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--report", default="reports/PG_PARALLEL_TUNE.json")
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
    print(f"쿼리 {len(prep)}건 · 컬렉션 {args.collection} · cand={_CAND_N}\n")

    # (이름, SQL, per_gather 워커, 병렬 강제)
    combos: list[tuple[str, str, int, bool]] = [
        ("현행 · 기본",          SQL_NOW,  2, False),
        ("현행 · 4워커",         SQL_NOW,  4, False),
        ("현행 · 4워커 · 강제",   SQL_NOW,  4, True),
        ("현행 · 6워커 · 강제",   SQL_NOW,  6, True),
        ("좁힘 · 기본",          SQL_LEAN, 2, False),
        ("좁힘 · 2워커 · 강제",   SQL_LEAN, 2, True),
        ("좁힘 · 4워커 · 강제",   SQL_LEAN, 4, True),
        ("좁힘 · 6워커 · 강제",   SQL_LEAN, 6, True),
    ]

    def sets(c, workers: int, force: bool) -> None:
        c.execute(text(f"SET max_parallel_workers_per_gather = {workers}"))
        if force:
            # 플래너가 병렬을 "비싸다" 고 보고 포기하는 것을 막는다
            c.execute(text("SET parallel_setup_cost = 0"))
            c.execute(text("SET parallel_tuple_cost = 0"))
            c.execute(text("SET min_parallel_table_scan_size = 0"))

    def run(sql: str, p: dict, workers: int, force: bool) -> tuple[float, list]:
        with vs._engine.connect() as c:
            sets(c, workers, force)
            t0 = time.perf_counter()
            got = c.execute(text(sql), p).fetchall()
            ms = (time.perf_counter() - t0) * 1000
        return ms, [(r[0], round(float(r[3]), 12)) for r in got]

    # 기준 결과(현행·기본)를 먼저 확보 - 나머지는 이것과 대조한다
    baseline_out = {i: run(SQL_NOW, p, 2, False)[1] for i, p in enumerate(prep)}

    print(f"  {'조합':<20s} {'p50':>9s} {'p95':>9s} {'평균':>9s} {'워커':>4s} "
          f"{'읽은블록':>9s}  {'동일':>5s}  정렬")
    out: dict = {"collection": args.collection, "n": len(prep), "runs": []}
    for name, sql, workers, force in combos:
        for p in prep[:5]:
            run(sql, p, workers, force)
        ts: list[float] = []
        diff = 0
        for i, p in enumerate(prep):
            ms, got = run(sql, p, workers, force)
            ts.append(ms)
            if got != baseline_out[i]:
                diff += 1
        with vs._engine.connect() as c:
            sets(c, workers, force)
            pj = c.execute(text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON){sql}"),
                           prep[0]).scalar()
            pj = pj[0] if isinstance(pj, list) else json.loads(pj)[0]
            info = _scan(pj["Plan"])
        same = f"{len(prep) - diff}/{len(prep)}"
        mb = (info["hit"] + info["read"]) * 8 / 1024
        print(f"  {name:<20s} {pct(ts, 0.5):>8.1f}ms {pct(ts, 0.95):>8.1f}ms "
              f"{st.mean(ts):>8.1f}ms {info['workers']:>4d} {mb:>7.0f}MB  {same:>5s}  "
              f"{' / '.join(info['sorts'])[:34]}")
        out["runs"].append({"name": name, "workers_setting": workers, "force": force,
                            "lean": sql is SQL_LEAN,
                            "p50": round(pct(ts, 0.5), 1), "p95": round(pct(ts, 0.95), 1),
                            "avg": round(st.mean(ts), 1),
                            "workers_launched": info["workers"],
                            "blocks_mb": round(mb, 1), "identical": diff == 0,
                            "n_differing": diff, "sorts": info["sorts"]})

    ok = [r for r in out["runs"] if r["identical"]]
    base = out["runs"][0]["p50"]
    best = min(ok, key=lambda r: r["p50"]) if ok else None
    print(f"\n  현행 p50 {base:.1f}ms   기준 200ms")
    if best:
        print(f"  결과가 같은 조합 중 최선: {best['name']}  p50 {best['p50']:.1f}ms "
              f"({best['p50'] - base:+.1f}ms)")
        print(f"  기준 200ms  →  {'통과' if best['p50'] <= 200 else '초과'}")
    out["baseline_p50"] = base
    out["best_identical"] = best
    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ 여기 값은 세션 SET 으로 잰 것이다. 채택하려면 서버 설정으로 고정해야 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

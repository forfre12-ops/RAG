"""ES→PG 게이트 **크럭스** 검증 — 실 Postgres `ts_rank_cd`가 프록시와 맞는지만 확인.

왜 이것만 따로?: 적대검증·NL 재검증이 남긴 **단일 핵심 불확실성**은
"파이썬 프록시(IDF 제거 BM25)로 잰 ts_rank-bigram ~87%가 **진짜 PG ts_rank**에서도 나오나"
뿐이다. 그런데 이건 **stock Postgres 16만으로** 측정 가능 — vector/pg_bigm 확장이 **불필요**
(dense 는 저장소 무관 기증명, pg_bigm 은 후보가속 부수기능). 즉 커스텀 이미지 빌드를
기다리지 않고 **기존 dev postgres(`docker compose up -d postgres`)** 만으로 게이트의 90%를 푼다.

측정: oss_corpus 를 bigram 토큰화(PgVectorStore._bigram)해 임시 테이블에 적재 →
NL(비발췌) 질의를 같은 토큰화로 `ts_rank_cd` 랭킹 → Recall@k(등급별). 프록시 NL@5(bigram
ts_rank≈87%, morph≈85%)와 대조. ±5pp 이내면 **ⓑ 확정**, 크게 낮으면 ⓒ(BM25 확장) 검토.

전제: stock Postgres 16 가동. usage:
  python scripts/revalidate_pg_tsrank_crux.py \
    --db postgresql://lloydk:lloydk_dev@localhost:5432/lloydk \
    --queries datasets/gold_real/retrieval_gold_nl.jsonl
(make revalidate-tsrank-crux 로도 실행)

⚠️ 라이브 PG 부재로 미실행 — 정적 작성. 임시 테이블(crux_lex)만 쓰고 끝에 DROP(비파괴).
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "datasets/oss_corpus"
GRADES = ["TS", "S1", "S2", "S3"]
TEXT_CAP = 4000


def san(s):
    return s.encode("utf-8", "ignore").decode("utf-8", "ignore") if isinstance(s, str) else ""


def load_corpus(limit=None):
    from lloydk.adapters.vectorstore.pg_store import PgVectorStore  # noqa: PLC0415
    ids, bigrams, grades = [], [], []
    files = sorted(glob.glob(str(CORPUS_DIR / "*.json")))
    if limit:
        files = files[:limit]
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        did = d.get("synth_id") or os.path.splitext(os.path.basename(f))[0]
        content = (san(d.get("title", "")) + " " + san(d.get("body", "")))[:TEXT_CAP]
        ids.append(did)
        bigrams.append(PgVectorStore._bigram(content))
        grades.append(d.get("target_grade", "?"))
    return ids, bigrams, grades


def recall_by_grade(hits, q_gold, q_grade, k):
    flags = [q_gold[i] in set(hits[i][:k]) for i in range(len(q_gold))]
    ov = sum(flags) / len(flags) if flags else 0.0
    by = {}
    for g in GRADES:
        idx = [i for i, qg in enumerate(q_grade) if qg == g]
        by[g] = (sum(flags[i] for i in idx) / len(idx)) if idx else 0.0
    return ov, by


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("DATABASE_URL", "postgresql://lloydk:lloydk_dev@localhost:5432/lloydk"))
    ap.add_argument("--queries", default=str(ROOT / "datasets/gold_real/retrieval_gold_nl.jsonl"))
    ap.add_argument("--limit-corpus", type=int, default=None)
    cli = ap.parse_args()
    dsn = cli.db.replace("postgresql+psycopg://", "postgresql://")

    import psycopg  # noqa: PLC0415
    from lloydk.adapters.vectorstore.pg_store import PgVectorStore  # noqa: PLC0415

    ids, bigrams, grades = load_corpus(cli.limit_corpus)
    print(f"[corpus] {len(ids)} docs bigram-tokenized")

    gold = [json.loads(l) for l in open(cli.queries, encoding="utf-8", errors="ignore") if l.strip()]
    q_text = [san(g["text"]) for g in gold]
    q_gold = [g["expected_doc_id"] for g in gold]
    q_grade = [g["expected_grade"] for g in gold]
    q_big = [PgVectorStore._bigram(t) for t in q_text]
    print(f"[queries] {len(gold)} ({os.path.basename(cli.queries)})  등급: "
          + "  ".join(f"{g}={sum(1 for x in q_grade if x==g)}" for g in GRADES))

    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute("DROP TABLE IF EXISTS crux_lex")
        cur.execute(
            """
            CREATE TABLE crux_lex (
                id          text PRIMARY KEY,
                grade       text,
                bigram_text text,
                tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(bigram_text, ''))) STORED
            )
            """
        )
        cur.execute("CREATE INDEX crux_tsv ON crux_lex USING gin (tsv)")
        with cur.copy("COPY crux_lex (id, grade, bigram_text) FROM STDIN") as cp:
            for i in range(len(ids)):
                cp.write_row((ids[i], grades[i], bigrams[i]))
        cur.execute("ANALYZE crux_lex")
        n = cur.execute("SELECT count(*) FROM crux_lex").fetchone()[0]
        print(f"[pg] crux_lex 적재 {n} (stock PG, 확장 불요)")

        # OR 의미(BM25 정합): bigram 토큰을 ' | ' 로 묶어 to_tsquery (plainto=AND라 패러프레이즈서 0매치).
        qors = [(" | ".join(qb.split()) or "__nomatch__") for qb in q_big]
        # ts_rank 변형 스윕 — IDF 없는 코어 채점의 천장 탐색. norm: 0=길이무시, 1=1+log(len),
        # 16=1+log(unique words), 32=rank/(rank+1). cd=cover density(근접) vs plain.
        VARIANTS = [
            ("ts_rank_cd n0", "ts_rank_cd(tsv, to_tsquery('simple', %(q)s))"),
            ("ts_rank_cd n1", "ts_rank_cd(tsv, to_tsquery('simple', %(q)s), 1)"),
            ("ts_rank_cd n16", "ts_rank_cd(tsv, to_tsquery('simple', %(q)s), 16)"),
            ("ts_rank    n0", "ts_rank(tsv, to_tsquery('simple', %(q)s))"),
            ("ts_rank    n1", "ts_rank(tsv, to_tsquery('simple', %(q)s), 1)"),
            ("ts_rank    n16", "ts_rank(tsv, to_tsquery('simple', %(q)s), 16)"),
        ]
        var_hits: dict[str, list] = {name: [] for name, _ in VARIANTS}
        for name, rankexpr in VARIANTS:
            sql = (f"SELECT id FROM crux_lex WHERE tsv @@ to_tsquery('simple', %(q)s) "
                   f"ORDER BY {rankexpr} DESC LIMIT 10")
            for qor in qors:
                cur.execute(sql, {"q": qor})
                var_hits[name].append([r[0] for r in cur.fetchall()])
        cur.execute("DROP TABLE IF EXISTS crux_lex")

    print("\n=== 실 PG ts_rank 변형별 Recall@5 (NL, bigram OR; IDF 없는 코어의 천장) ===")
    best_name, best = None, 0.0
    for name, _ in VARIANTS:
        ov, by = recall_by_grade(var_hits[name], q_gold, q_grade, 5)
        if ov > best:
            best_name, best = name, ov
        print(f"  {name:16s} 전체={ov*100:>3.0f}%  " + "  ".join(f"{g}={by[g]*100:>3.0f}%" for g in GRADES))
    print("\n[프록시 NL@5] bigram ts_rank(idf=1 근사)≈87% · morph≈85%")
    print(f"[실 PG 최선] {best_name} = {best*100:.0f}%   Δ(실−프록시)={best*100-87:+.0f}pp")
    print("[해석] PG 코어 ts_rank 는 IDF 부재 → 어떤 변형도 IDF 프록시를 못 따라감이 확인되면")
    print("       어휘 nori-동급엔 ⓒ(BM25 확장) 또는 앱사이드 BM25 필요. (dense 결합은 별도 측정)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""ES→PG 단일화 재검증 — **실 Postgres 연산자**(ts_rank/pg_bigm)로 어휘검색 품질 측정.

의사결정_대장 §03 게이트. 프록시 벤치(scripts/_bench_pg_lexical_revalidation.py, 파이썬
인프로세스 BM25)가 "토큰화는 위험 아님, IDF 채점이 위험"을 보였고 적대검증이 "프록시 IDF는
PG 네이티브 아님"을 지적 → 본 스크립트로 **실 PG 연산자**를 직접 찍어 확정한다.

측정 arm (실 PG):
  - dense          : pgvector 코사인 (store.search)
  - hybrid(ⓑ)      : ts_rank_cd + pg_bigm 후보 RRF (store.search_hybrid)
  - raw pg_bigm    : bigm_similarity 단독 랭킹 (붕괴 확인용; 직접 SQL)

전제 (infra/postgres/README.md 참조):
  - docker compose -f docker-compose.pgvector.yml up -d --build  (pgvector+pg_bigm, :5433)
  - alembic upgrade head  (tb_rag_vectors 생성)
  - 환경: VECTOR_BACKEND=pg, DATABASE_URL=...localhost:5433...

⚠️ 라이브 PG 미가동 상태로 작성 — 미검증. 최초 실행 시 결과/SQL 오류를 확인할 것.

합격 게이트:
  - **자연어(비발췌) 쿼리** 세트에서 hybrid(ⓑ) R@5 가 nori 기준 대비 ~5pp 이내 → 경로 ⓑ 확정.
  - >10pp 퇴행 → 경로 ⓒ(BM25 확장: ParadeDB/pg_search·VectorChord-bm25) 승급.
  - retrieval_gold(발췌)는 1차 sanity(프록시와 대조)용 — gate 판정은 NL 쿼리로.

usage:
  VECTOR_BACKEND=pg DATABASE_URL=postgresql+psycopg://koipa:koipa_dev@localhost:5433/koipa \
    python scripts/revalidate_pg_lexical.py [--queries datasets/gold_real/retrieval_gold.jsonl] [--limit-corpus N]
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "datasets/oss_corpus"
EMB = "nlpai-lab/KURE-v1"
COLLECTION = "revalid"
GRADES = ["TS", "S1", "S2", "S3"]
EMB_CACHE = ROOT / "reports/revalid_pg_corpus_embeds.npz"


def san(s):
    return s.encode("utf-8", "ignore").decode("utf-8", "ignore") if isinstance(s, str) else ""


def load_corpus(limit=None):
    ids, texts, grades = [], [], []
    files = sorted(glob.glob(str(CORPUS_DIR / "*.json")))
    if limit:
        files = files[:limit]
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        ids.append(d.get("synth_id") or os.path.splitext(os.path.basename(f))[0])
        texts.append((san(d.get("title", "")) + " " + san(d.get("body", ""))).strip())
        grades.append(d.get("target_grade", "?"))
    return ids, texts, grades


def embed_corpus(texts):
    sig = f"kure|{len(texts)}"
    if EMB_CACHE.exists() and str(np.load(EMB_CACHE, allow_pickle=True).get("sig")) == sig:
        return np.load(EMB_CACHE, allow_pickle=True)["C"]
    import torch  # noqa: PLC0415
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[embed] {EMB} on {dev} …", flush=True)
    m = SentenceTransformer(EMB, device=dev)
    m.max_seq_length = 512
    C = np.asarray(m.encode(texts, normalize_embeddings=True, batch_size=16, show_progress_bar=True))
    EMB_CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(EMB_CACHE, C=C, sig=sig)
    return C


def recall_by_grade(hit_ids_per_q, q_gold, q_grade, k):
    flags = [q_gold[i] in set(hit_ids_per_q[i][:k]) for i in range(len(q_gold))]
    ov = sum(flags) / len(flags) if flags else 0.0
    by = {}
    for g in GRADES:
        idx = [i for i, qg in enumerate(q_grade) if qg == g]
        by[g] = (sum(flags[i] for i in idx) / len(idx)) if idx else 0.0
    return ov, by


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default=str(ROOT / "datasets/gold_real/retrieval_gold.jsonl"))
    ap.add_argument("--limit-corpus", type=int, default=None)
    ap.add_argument("--reindex", action="store_true", help="기존 collection 비우고 재적재")
    args = ap.parse_args()

    if os.getenv("VECTOR_BACKEND", "").lower() not in ("pg", "pgvector", "postgres"):
        print("ERROR: VECTOR_BACKEND=pg 로 설정하고 DATABASE_URL 을 :5433 PG 로 지정하세요.", file=sys.stderr)
        return 2

    from koipa.adapters.vectorstore import build_store  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    store = build_store()
    print(f"[store] {type(store).__name__}")

    ids, texts, grades = load_corpus(args.limit_corpus)
    print(f"[corpus] {len(ids)} docs")

    # 적재 (멱등: ON CONFLICT). --reindex 면 먼저 비움.
    if args.reindex:
        store.delete(COLLECTION, filter={"tenant_id": "revalid"})
    n_existing = store.count(COLLECTION)
    if n_existing < len(ids) or args.reindex:
        C = embed_corpus(texts)
        store.ensure_collection(COLLECTION, C.shape[1])
        payloads = [{"id": ids[i], "doc_id": ids[i], "tenant_id": "revalid",
                     "chunk_idx": 0, "text": texts[i], "grade": grades[i]} for i in range(len(ids))]
        B = 500
        for s in range(0, len(ids), B):
            store.upsert(COLLECTION, ids[s:s + B], [list(v) for v in C[s:s + B]], payloads[s:s + B])
            print(f"  upsert {min(s + B, len(ids))}/{len(ids)}", flush=True)
    print(f"[store] collection '{COLLECTION}' count={store.count(COLLECTION)}")

    # 쿼리 (임베딩은 store.search 가 벡터 요구 → 질의 임베딩 필요)
    gold = [json.loads(l) for l in open(args.queries, encoding="utf-8", errors="ignore") if l.strip()]
    q_text = [san(g["text"]) for g in gold]
    q_gold = [g["expected_doc_id"] for g in gold]
    q_grade = [g["expected_grade"] for g in gold]
    import torch  # noqa: PLC0415
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    qm = SentenceTransformer(EMB, device=dev)
    qm.max_seq_length = 512
    Q = np.asarray(qm.encode(q_text, normalize_embeddings=True, batch_size=16, show_progress_bar=False))

    dense_hits, hybrid_hits, bigm_hits = [], [], []
    for i in range(len(gold)):
        qv = [float(x) for x in Q[i]]
        dense_hits.append([h.id for h in store.search(COLLECTION, qv, top_k=10)])
        hybrid_hits.append([h.id for h in store.search_hybrid(COLLECTION, q_text[i], qv, top_k=10)])
        # raw pg_bigm 단독 (붕괴 확인) — 직접 SQL
        with store._engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id FROM tb_rag_vectors WHERE collection = :c "
                "ORDER BY bigm_similarity(content, :q) DESC LIMIT 10"
            ), {"c": COLLECTION, "q": q_text[i]})
            bigm_hits.append([str(r[0]) for r in rows])

    print(f"\n=== 실 PG 연산자 Recall (queries={len(gold)}; {os.path.basename(args.queries)}) ===")
    for name, hits in (("dense(pgvector)", dense_hits),
                       ("hybrid ts_rank+pg_bigm(ⓑ)", hybrid_hits),
                       ("raw pg_bigm sim", bigm_hits)):
        for k in (1, 5, 10):
            ov, by = recall_by_grade(hits, q_gold, q_grade, k)
            print(f"  {name:28s} R@{k:<2} 전체={ov*100:>3.0f}%  "
                  + "  ".join(f"{g}={by[g]*100:>3.0f}%" for g in GRADES))
        print()

    OUT = ROOT / "reports/revalidate_pg_lexical.json"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "queries": args.queries, "n_queries": len(gold), "corpus_n": len(ids),
        "arms": {
            name: {f"R@{k}": recall_by_grade(hits, q_gold, q_grade, k)[0] for k in (1, 5, 10)}
            for name, hits in (("dense", dense_hits), ("hybrid", hybrid_hits), ("raw_pg_bigm", bigm_hits))
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"리포트 → {OUT}")
    print("\n[게이트] NL(비발췌) 쿼리에서 hybrid R@5 가 nori~94% 대비 ±5pp → ⓑ 확정 / >10pp 퇴행 → ⓒ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

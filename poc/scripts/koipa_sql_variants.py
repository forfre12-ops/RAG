"""hybrid 검색 SQL 두 판 - 지연 실험에서 공유한다.

측정 스크립트가 각자 SQL 을 복사해 두면 한쪽만 고쳐져 서로 다른 것을 재게 된다.
그래서 여기 한 곳에 둔다.

SQL_NOW    출하본과 같다 (`pg_store.search_hybrid`). 필터(fwhere)는 실험에서 안 쓴다.
SQL_LEAN   CTE 가 id 와 순위만 들고, payload·content 는 마지막에 기본키로 되받는다.
           ts_rank 식·RRF 식·후보 수가 같으므로 랭킹은 안 바뀌어야 한다 -
           그건 확인 대상이라 각 스크립트가 반환 id·점수를 대조한다.

⚠ SQL_NOW 를 고치면 `pg_store.search_hybrid` 와 어긋난다. 둘은 같아야 한다.
"""
from __future__ import annotations

SQL_NOW = """
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

SQL_LEAN = """
WITH d AS (
    SELECT id, row_number() OVER (ORDER BY embedding <=> (:q)::vector) AS rn
    FROM tb_rag_vectors
    WHERE collection = :collection AND embedding IS NOT NULL
    ORDER BY embedding <=> (:q)::vector
    LIMIT :cand
),
l AS (
    SELECT id, row_number() OVER (
               ORDER BY ts_rank(tsv, to_tsquery('simple', :qt), 1) DESC
           ) AS rn
    FROM tb_rag_vectors
    WHERE collection = :collection
      AND tsv @@ to_tsquery('simple', :qt)
    LIMIT :cand
),
fused AS (
    SELECT COALESCE(d.id, l.id) AS id,
           COALESCE(1.0 / (:k + d.rn), 0) + COALESCE(1.0 / (:k + l.rn), 0) AS score
    FROM d FULL OUTER JOIN l ON d.id = l.id
    ORDER BY score DESC
    LIMIT :topk
)
SELECT f.id, v.payload, v.content, f.score
FROM fused f
JOIN tb_rag_vectors v ON v.collection = :collection AND v.id = f.id
ORDER BY f.score DESC
"""

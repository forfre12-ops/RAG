"""PG-native RAG 벡터스토어 — pgvector(dense) + tsvector/ts_rank + pg_bigm(후보).

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-06-24

의사결정_대장 §03 (ES→Postgres 단일화, 경로 ⓑ: ts_rank 코어 + pg_bigm 후보)의 코드化.
실측 근거: scripts/_bench_pg_lexical_revalidation.py (ts_rank 코어 R@5 ~80-85%).

⚠️ 전제: 이 마이그레이션은 **pgvector 확장이 포함된 PG 이미지**를 요구한다
   (stock postgres:16-alpine 에는 vector/pg_bigm 미포함 — docker-compose 의 postgres
    이미지를 pgvector/pgvector:pg16 등 커스텀 빌드로 교체해야 적용 가능).
   pg_bigm 은 *선택*(후보 가속용) — 미설치 환경에서도 마이그레이션이 깨지지 않도록
   DO/EXCEPTION 으로 가드하고, 부재 시 bigm GIN 인덱스를 건너뛴다(tsv/ts_rank 경로는
   pg_bigm 없이도 동작).

⚠️ 상태: ES 폐기는 §03 재검증 게이트(실 PG연산자×실코퍼스×자연어쿼리) 통과 전 보류.
   본 마이그레이션은 신규 테이블만 추가하므로 기존 스키마/ES 경로에 비파괴.

임베딩 차원: KURE-v1 / BGE-M3 = 1024 고정. 임베딩 모델 차원이 바뀌면 vector(1024)도
   변경 필요(벡터 컬럼 차원은 가변 불가).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBED_DIM = 1024


def upgrade() -> None:
    # pgvector — 필수 (route ⓑ dense). 이미지에 없으면 여기서 실패(=올바른 fail-fast).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # pg_bigm — 선택(후보 가속). 부재 시 NOTICE 후 진행.
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS pg_bigm;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pg_bigm 미설치 — bigm 후보 인덱스 생략(tsv/ts_rank 경로는 정상). %', SQLERRM;
        END$$;
        """
    )

    # RAG 벡터 단일 테이블. ES 의 '인덱스(collection)'를 collection 컬럼으로 표현.
    # 비파티션 — tb_chunks(created_at RANGE 파티션)와 분리해 HNSW 인덱스 파티션 복잡도 회피.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS tb_rag_vectors (
            collection  TEXT        NOT NULL,
            id          TEXT        NOT NULL,
            doc_id      TEXT,
            tenant_id   TEXT,
            chunk_idx   INTEGER,
            content     TEXT        NOT NULL,
            embedding   vector({EMBED_DIM}),
            -- ts_rank 채점 채널. 한국어 'simple' config = 무분석기(어절). 형태소 사전토큰화가
            -- 필요하면 앱사이드에서 토큰을 공백조인해 content 에 적재(또는 별도 컬럼) — 본 경로(ⓑ)는
            -- 무분석기 ts_rank(~80-85%) 수용. GENERATED 라 적재 시 자동 갱신.
            tsv         tsvector    GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content, ''))) STORED,
            payload     JSONB       NOT NULL DEFAULT '{{}}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (collection, id)
        )
        """
    )

    # dense kNN (코사인). HNSW — pgvector 0.5+.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ragvec_embedding "
        "ON tb_rag_vectors USING hnsw (embedding vector_cosine_ops)"
    )
    # ts_rank 어휘 채널.
    op.execute("CREATE INDEX IF NOT EXISTS idx_ragvec_tsv ON tb_rag_vectors USING gin (tsv)")
    # pg_bigm 후보생성 가속(선택) — 확장 있을 때만.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_bigm') THEN
                CREATE INDEX IF NOT EXISTS idx_ragvec_content_bigm
                    ON tb_rag_vectors USING gin (content gin_bigm_ops);
            END IF;
        END$$;
        """
    )
    # 스코프 필터 hot path.
    op.execute("CREATE INDEX IF NOT EXISTS idx_ragvec_coll_tenant ON tb_rag_vectors (collection, tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_ragvec_coll_doc ON tb_rag_vectors (collection, doc_id)")

    # alias → collection (무중단 재색인 blue/green). ES alias swap 대체.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tb_rag_aliases (
            alias       TEXT        PRIMARY KEY,
            collection  TEXT        NOT NULL,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tb_rag_aliases")
    op.execute("DROP INDEX IF EXISTS idx_ragvec_coll_doc")
    op.execute("DROP INDEX IF EXISTS idx_ragvec_coll_tenant")
    op.execute("DROP INDEX IF EXISTS idx_ragvec_content_bigm")
    op.execute("DROP INDEX IF EXISTS idx_ragvec_tsv")
    op.execute("DROP INDEX IF EXISTS idx_ragvec_embedding")
    op.execute("DROP TABLE IF EXISTS tb_rag_vectors")
    # 확장(vector/pg_bigm)은 drop 하지 않음 — 다른 객체가 의존할 수 있음.

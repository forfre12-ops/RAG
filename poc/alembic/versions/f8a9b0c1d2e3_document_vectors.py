"""유사문서 조회용 문서 벡터 표 — pgvector dense(HNSW, 코사인).

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-09-09

왜 다시 세우는가. 2026-09-04(319069b9)에 유사문서 조회를 코드·DB에서 걷어냈고, 그때
`tb_rag_vectors`·`tb_rag_aliases` 도 같이 뺐다(a3b4c5d6e7f8). 고객사가 유사문서 검색을
요구해 되살리되, **그때 것을 그대로 되돌리지 않는다.**

■ 되살리는 것과 아닌 것

    되살린다   pgvector 확장 · 문서 벡터 표 · HNSW 코사인 인덱스
    안 되살린다 collection/alias(무중단 재색인 blue-green) — 이 규모에 불필요
               bigram_text/tsv/ts_rank(어휘 채널) · pg_bigm — dense-only 로 시작한다
               rag_used·rag_top_k 등 질의응답용 칼럼 9개 — /answer 는 복원하지 않는다

  어휘 채널을 뺀 이유는 성능이 아니라 순서다. 2026-06 실측에서 하이브리드(dense+bigram
  ts_rank)가 R@5 85% 로 dense-only 76% 보다 나았다. 그 +9pp 가 필요해지면 그때 tsv 칼럼과
  GIN 인덱스를 얹는 판을 하나 더 만든다 — 지금 넣으면 쓰지 않는 채널을 운영하게 된다.

■ 청크가 아니라 **문서 하나에 벡터 하나**

  기능이 "유사 **문서** 조회"다. 청크 단위로 담으면 검색 결과를 다시 문서로 접어야 하고
  인덱스가 수십 배가 된다. 문서 대표 벡터(청크 임베딩 평균)를 담는다. 그래서 PK 가
  doc_id 이고, 한 문서는 최대 한 행이다.

■ 삭제 — FK 만으로는 모자란다

  `ON DELETE CASCADE` 는 **하드 삭제**에만 걸린다. tb_documents 는 soft delete
  (`deleted_at`)를 쓰므로, 검수자가 문서를 지워도 이 표의 행은 남는다. 그래서 조회 쪽이
  반드시 `tb_documents.deleted_at IS NULL` 로 걸러야 한다 — 안 걸면 **지운 문서가 유사
  문서로 뜨고, 그 옆에 등급까지 붙어 나온다.** CASCADE 는 하드 삭제·재적재 때 고아 행이
  남지 않게 하는 두 번째 방어선이다.

■ 등급은 여기 담지 않는다

  확정 등급은 tb_document_labels 에 있고 검수로 바뀐다. 이 표에 복제하면 두 값이
  갈라지고, 갈라지면 화면이 **틀린 등급을 유사 문서 옆에 보여준다.** 조회 시 조인한다.

■ 전제

  pgvector 확장이 있는 이미지여야 한다(docker-compose 의 pgvector/pgvector:pg16).
  없으면 이 판이 CREATE EXTENSION 에서 실패한다 — 옳은 fail-fast 다.

  임베딩 차원 1024 는 KURE-v1 실측값이다(scripts/build_offline_bundle.py 의 모델 표와
  같은 값). 임베딩 모델을 바꿔 차원이 달라지면 이 칼럼도 새 판으로 바꿔야 한다
  (vector 칼럼의 차원은 ALTER 로 못 바꾼다) — 그래서 model 칼럼에 어느 모델로 만든
  벡터인지 남긴다. 재색인 대상을 그 값으로 고른다.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# KURE-v1 / BGE-M3 = 1024. 모델을 바꿔 차원이 달라지면 새 판이 필요하다.
EMBED_DIM = 1024


def upgrade() -> None:
    # 이미지에 pgvector 가 없으면 여기서 실패한다 — 빈 표를 만들어 두고 검색이 조용히
    # 안 되는 것보다, 배포가 못 서는 쪽이 낫다.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS tb_document_vectors (
            doc_id         UUID         PRIMARY KEY
                           REFERENCES tb_documents(doc_id) ON DELETE CASCADE,
            embedding      vector({EMBED_DIM}) NOT NULL,
            -- 어느 임베딩 모델로 만든 벡터인가. 모델을 바꾸면 이 값으로 재색인 대상을 고른다.
            model          TEXT         NOT NULL,
            -- 평균에 들어간 청크 수. 0 이면 본문을 못 읽은 것이라 색인 실패로 본다.
            chunk_count    INTEGER      NOT NULL DEFAULT 0,
            -- 본문 해시. 같은 문서가 다시 올라왔을 때 재색인이 필요한지 판단한다.
            content_sha256 TEXT,
            created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )

    # dense kNN — 코사인. HNSW 는 pgvector 0.5+.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_docvec_embedding "
        "ON tb_document_vectors USING hnsw (embedding vector_cosine_ops)"
    )
    # 모델별 재색인 대상 조회용.
    op.execute("CREATE INDEX IF NOT EXISTS idx_docvec_model ON tb_document_vectors (model)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_docvec_model")
    op.execute("DROP INDEX IF EXISTS idx_docvec_embedding")
    op.execute("DROP TABLE IF EXISTS tb_document_vectors")
    # vector 확장은 지우지 않는다 — 다른 객체가 의존할 수 있다.

"""문서 대표 벡터 저장·검색 (pgvector dense · 코사인).

■ 왜 문서 하나에 벡터 하나인가

  기능이 "유사 **문서** 조회"다. 청크마다 담으면 검색 결과가 청크라 다시 문서로 접어야
  하고 인덱스가 수십 배가 된다. 문서 대표 벡터(청크 임베딩 평균)를 담는다.

■ 왜 등급 조인이 이 모듈 안에 있는가

  "유사 문서 + 확정 등급"을 한 화면에 보여주는 것이 요구다. 벡터와 등급을 같은
  PostgreSQL 안에 두기로 한 이유가 **그 둘을 한 쿼리로 묶기 위해서**다(그래서 MariaDB
  + 별도 벡터DB 대신 PG + pgvector 로 되돌렸다). 조회를 두 단계로 쪼개면 그 이점을
  스스로 버리는 셈이라, 조인을 여기 둔다.

  ⛔ 등급을 tb_document_vectors 에 **복제하지 않는다.** 확정 등급은 검수로 바뀌는 값이라
     복제하면 두 값이 갈라지고, 갈라지면 화면이 틀린 등급을 유사 문서 옆에 보여준다.
     항상 tb_document_labels 를 조인해서 읽는다.

■ 삭제

  tb_documents 는 soft delete(`deleted_at`)다. FK ON DELETE CASCADE 는 하드 삭제에만
  걸리므로, 조회에 `d.deleted_at IS NULL` 이 **반드시** 들어가야 한다. 빠지면 검수자가
  지운 문서가 유사 문서로 뜨고 그 옆에 등급까지 붙는다.

■ pgvector 파이썬 패키지는 쓰지 않는다

  벡터는 텍스트 리터럴 `'[a,b,...]'::vector` 로 주고받는다. 의존성이 하나 줄고,
  이 표가 ORM 밖(alembic/env.py `_MIGRATION_ONLY_TABLES`)인 것과도 맞는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 임베딩 차원 — 마이그레이션 f8a9b0c1d2e3 의 vector(1024) 와 같은 값이어야 한다.
EMBED_DIM = 1024


@dataclass(frozen=True)
class SimilarDocument:
    """유사 문서 한 건 — 벡터에서 온 거리 + RDBMS 에서 온 사실."""

    doc_id: str
    filename: str
    distance: float          # 코사인 거리(0=같음, 1=직교, 2=반대)
    grade: str | None        # 확정 등급 코드(TS/S1/S2/S3). 미검수면 None
    is_verified: bool        # 사람이 확정했는가
    verified_at: str | None

    @property
    def similarity(self) -> float:
        """1 - 거리. 화면에 "얼마나 비슷한가"로 보일 값."""
        return 1.0 - self.distance


class DocumentVectorStore:
    """tb_document_vectors 읽기·쓰기. 지연 연결 — 생성 시 DB 에 붙지 않는다."""

    def __init__(self, engine: Any | None = None) -> None:
        if engine is None:
            from koipa.db.session import engine as _engine  # noqa: PLC0415
            engine = _engine
        self._engine = engine

    # ── 벡터 리터럴 ──────────────────────────────────────────────────────────
    @staticmethod
    def _vec_lit(vec: Sequence[float]) -> str:
        return "[" + ",".join(repr(float(x)) for x in vec) + "]"

    # ── 쓰기 ────────────────────────────────────────────────────────────────
    def upsert(
        self,
        *,
        doc_id: str,
        embedding: Sequence[float],
        model: str,
        chunk_count: int = 0,
        content_sha256: str | None = None,
    ) -> None:
        """문서 대표 벡터를 넣거나 덮는다. 같은 문서를 다시 올리면 최신 것이 이긴다."""
        if len(embedding) != EMBED_DIM:
            raise ValueError(
                f"임베딩 차원이 표와 다르다: {len(embedding)} != {EMBED_DIM}. "
                "임베딩 모델을 바꿨다면 마이그레이션도 함께 바꿔야 한다"
                "(vector 칼럼의 차원은 ALTER 로 못 바꾼다)."
            )
        stmt = text(
            """
            INSERT INTO tb_document_vectors
                   (doc_id, embedding, model, chunk_count, content_sha256)
            VALUES (:doc_id, CAST(:emb AS vector), :model, :n, :sha)
            ON CONFLICT (doc_id) DO UPDATE SET
                   embedding      = EXCLUDED.embedding,
                   model          = EXCLUDED.model,
                   chunk_count    = EXCLUDED.chunk_count,
                   content_sha256 = EXCLUDED.content_sha256,
                   created_at     = now()
            """
        )
        with self._engine.begin() as conn:
            conn.execute(stmt, {
                "doc_id": str(doc_id), "emb": self._vec_lit(embedding),
                "model": model, "n": int(chunk_count), "sha": content_sha256,
            })

    def delete(self, doc_id: str) -> None:
        """벡터를 지운다. 하드 삭제는 FK CASCADE 가 처리하므로, 이 함수는 재색인·
        soft delete 정리처럼 문서는 남기고 벡터만 뺄 때 쓴다."""
        with self._engine.begin() as conn:
            conn.execute(
                text("DELETE FROM tb_document_vectors WHERE doc_id = :d"),
                {"d": str(doc_id)},
            )

    # ── 읽기 ────────────────────────────────────────────────────────────────
    def needs_index(self, doc_id: str, content_sha256: str | None) -> bool:
        """이 문서를 (다시) 색인해야 하는가.

        본문 해시가 같으면 건너뛴다 — 같은 문서를 다시 올렸을 때 임베딩을 다시 돌리지
        않기 위한 것이다(청크당 0.5초, 100쪽이면 2분).
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT content_sha256 FROM tb_document_vectors WHERE doc_id = :d"),
                {"d": str(doc_id)},
            ).first()
        if row is None:
            return True
        return content_sha256 is None or row[0] != content_sha256

    def exists(self, doc_id: str) -> bool:
        """이 문서의 벡터가 있는가 — "색인 전"과 "비슷한 문서가 없음"을 가르는 데 쓴다.

        둘은 화면에서 다르게 읽혀야 한다. 색인 전이면 "잠시 후 다시", 비교 상대가 없으면
        "비슷한 문서 없음"이다.
        """
        with self._engine.connect() as conn:
            return conn.execute(
                text("SELECT 1 FROM tb_document_vectors WHERE doc_id = :d"),
                {"d": str(doc_id)},
            ).first() is not None

    def count(self) -> int:
        with self._engine.connect() as conn:
            return int(conn.execute(text("SELECT count(*) FROM tb_document_vectors")).scalar_one())

    def similar(self, doc_id: str, *, k: int = 5, verified_only: bool = False) -> list[SimilarDocument]:
        """기준 문서와 비슷한 문서 상위 k건 — 확정 등급을 함께 돌려준다.

        기준 문서 자신은 빼고 돌려준다(코사인 거리 0 이라 항상 1등으로 나온다).
        기준 문서의 벡터가 아직 없으면 빈 목록이다 — 색인 전이라는 뜻이고, 오류가 아니다.

        verified_only=True 면 **사람이 확정한 등급이 있는 문서만** 돌려준다. 화면이
        "비슷한 문서는 이 등급을 받았습니다"로 읽히는 자리에서는 이쪽을 쓴다 —
        기계가 매긴 등급을 근거처럼 보여주면 안 된다.
        """
        stmt = text(
            f"""
            SELECT d.doc_id::text          AS doc_id,
                   d.filename              AS filename,
                   (v.embedding <=> q.embedding)::float8 AS distance,
                   cl.level_code           AS grade,
                   COALESCE(l.is_verified, FALSE) AS is_verified,
                   l.verified_at           AS verified_at
              FROM tb_document_vectors v
              JOIN tb_documents  d  ON d.doc_id = v.doc_id
              JOIN tb_document_vectors q ON q.doc_id = :doc_id
              LEFT JOIN tb_document_labels l ON l.doc_id = v.doc_id
              LEFT JOIN tb_classification_levels cl ON cl.level_id = l.level_id
             WHERE v.doc_id <> :doc_id
               AND d.deleted_at IS NULL        -- soft delete 는 CASCADE 가 안 잡는다
               {"AND COALESCE(l.is_verified, FALSE) = TRUE" if verified_only else ""}
             ORDER BY distance
             LIMIT :k
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt, {"doc_id": str(doc_id), "k": int(k)}).mappings().all()
        return [
            SimilarDocument(
                doc_id=r["doc_id"],
                filename=r["filename"],
                distance=float(r["distance"]),
                grade=r["grade"],
                is_verified=bool(r["is_verified"]),
                verified_at=r["verified_at"].isoformat() if r["verified_at"] else None,
            )
            for r in rows
        ]

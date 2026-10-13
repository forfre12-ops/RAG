"""규정 참고 표시 — 규정·조항·문장 표 3개 접근.

트랜잭션 경계는 호출자다(세션에 add·flush 만 한다 — 다른 리포지토리와 같다).

■ 삭제
    `delete_children` 은 조항·문장 행을 물리 삭제하고 판 행은 tombstone(del_dt)으로 남긴다.
    판 행에는 벡터·원문이 없고(원본 파일과 조항·문장이 지워진다), 부분 UNIQUE 인덱스가 삭제한 판을 제외하므로
    같은 파일을 다시 올릴 수 있다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from koipa.db.models import Regulation, RegulationClause, RegulationSentence
from koipa.regulation import status as st
from koipa.regulation.splitter import Clause, compose_embed_text


@dataclass(frozen=True)
class ClausePlan:
    """분할·태깅을 마친 조항 한 건 — 저장 전 상태."""
    clause: Clause
    kind: str
    kind_source: str
    display: bool


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class RegulationRepo:
    def __init__(self, db: Session):
        self.db = db

    # ── 판 ────────────────────────────────────────────────────────────────
    def create(self, *, name: str, version_label: str, file_hash: str, filename: str, source_format: str,
               raw_uri: str | None, effective_date: str | None, created_by: str | None) -> Regulation:
        row = Regulation(name=name, version_label=version_label, file_hash=file_hash, filename=filename,
                         source_format=source_format, raw_uri=raw_uri, effective_date=effective_date,
                         created_by=created_by, status=st.INDEXING)
        self.db.add(row)
        self.db.flush()
        return row

    def get(self, reg_id: str | uuid.UUID) -> Regulation | None:
        """삭제하지 않은 판만."""
        return self.db.execute(
            select(Regulation).where(Regulation.id == _uuid(reg_id), Regulation.deleted_at.is_(None))
        ).scalar_one_or_none()

    def find_live_by_hash(self, file_hash: str) -> Regulation | None:
        return self.db.execute(
            select(Regulation).where(Regulation.file_hash == file_hash, Regulation.deleted_at.is_(None))
        ).scalar_one_or_none()

    def list(self, *, status: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[Regulation], int]:
        q = select(Regulation).where(Regulation.deleted_at.is_(None))
        if status:
            q = q.where(Regulation.status == status)
        total = self.db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
        rows = self.db.execute(
            q.order_by(Regulation.created_at.desc(), Regulation.id).limit(limit).offset(offset)
        ).scalars().all()
        return list(rows), int(total)

    def active(self) -> list[Regulation]:
        return list(self.db.execute(
            select(Regulation).where(Regulation.status == st.ACTIVE, Regulation.deleted_at.is_(None))
            .order_by(Regulation.name, Regulation.version_label, Regulation.id)
        ).scalars().all())

    def active_signature(self) -> tuple[int, str]:
        """조회 캐시 무효화 판별용 — 활성 판의 (개수, 각 판의 id·수정 시각 묶음). 바뀌면 캐시를 버린다."""
        rows = self.db.execute(
            select(Regulation.id, Regulation.updated_at)
            .where(Regulation.status == st.ACTIVE, Regulation.deleted_at.is_(None))
            .order_by(Regulation.id)
        ).all()
        return len(rows), "|".join(f"{r[0]}@{r[1].isoformat() if r[1] else ''}" for r in rows)

    def activate(self, reg: Regulation, *, scope_note: str | None) -> list[Regulation]:
        """활성화. **같은 규정명의 이전 활성 판은 같은 트랜잭션에서 보관으로 바뀐다.** 보관된 판 목록을 돌려준다."""
        now = dt.datetime.now(dt.timezone.utc)
        others = list(self.db.execute(
            select(Regulation).where(Regulation.name == reg.name, Regulation.status == st.ACTIVE,
                                     Regulation.id != reg.id, Regulation.deleted_at.is_(None))
        ).scalars().all())
        for o in others:
            o.status, o.archived_at, o.updated_at = st.ARCHIVED, now, now
        reg.status, reg.activated_at, reg.archived_at, reg.updated_at = st.ACTIVE, now, None, now
        reg.scope_confirmed = True
        reg.scope_note = scope_note
        self.db.flush()
        return others

    def archive(self, reg: Regulation) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        reg.status, reg.archived_at, reg.updated_at = st.ARCHIVED, now, now
        self.db.flush()

    def count_active_names(self, exclude_id: uuid.UUID | None = None) -> int:
        """활성 판이 있는 **서로 다른 규정명** 수 — 활성 상한(계열 단위)에 쓴다."""
        q = select(func.count(func.distinct(Regulation.name))).where(
            Regulation.status == st.ACTIVE, Regulation.deleted_at.is_(None))
        if exclude_id is not None:
            q = q.where(Regulation.id != exclude_id)
        return int(self.db.execute(q).scalar_one())

    def soft_delete(self, reg: Regulation) -> None:
        """조항·문장을 물리 삭제하고 판 행은 tombstone 으로 남긴다(원본 파일 삭제는 서비스 몫)."""
        self.delete_children(reg.id)
        now = dt.datetime.now(dt.timezone.utc)
        reg.deleted_at, reg.updated_at = now, now
        reg.clause_count = reg.sentence_count = reg.embedded_count = reg.embed_target_count = 0
        self.db.flush()

    # ── 조항·문장 ──────────────────────────────────────────────────────────
    def delete_children(self, reg_id: uuid.UUID) -> None:
        self.db.execute(delete(RegulationClause).where(RegulationClause.regulation_id == _uuid(reg_id)))
        self.db.flush()      # 문장은 조항 FK ON DELETE CASCADE

    def replace_clauses(self, reg_id: str | uuid.UUID, plans: Sequence[ClausePlan]) -> None:
        """조항·문장 행을 통째로 다시 만든다(재시도 안전 — 이전 행은 지운다)."""
        rid = _uuid(reg_id)
        self.delete_children(rid)
        for p in plans:
            c = p.clause
            row = RegulationClause(regulation_id=rid, seq=c.seq, article_no=c.article_no[:50], title=c.title[:300],
                                   chapter=c.chapter[:300], text_=c.text, kind=p.kind,
                                   kind_source=p.kind_source, display=p.display)
            self.db.add(row)
            self.db.flush()
            for s in c.sentences:
                self.db.add(RegulationSentence(clause_id=row.id, seq=s.seq, text_=s.text, is_lead=s.is_lead,
                                               list_group=s.list_group))
        self.db.flush()

    def list_clauses(self, reg_id: str | uuid.UUID, *, kind: str | None = None, display: bool | None = None,
                     limit: int = 200, offset: int = 0) -> tuple[list[RegulationClause], int]:
        q = select(RegulationClause).where(RegulationClause.regulation_id == _uuid(reg_id))
        if kind:
            q = q.where(RegulationClause.kind == kind)
        if display is not None:
            q = q.where(RegulationClause.display.is_(display))
        total = self.db.execute(select(func.count()).select_from(q.subquery())).scalar_one()
        rows = self.db.execute(q.order_by(RegulationClause.seq).limit(limit).offset(offset)).scalars().all()
        return list(rows), int(total)

    def get_clause(self, reg_id: str | uuid.UUID, clause_id: str | uuid.UUID) -> RegulationClause | None:
        return self.db.execute(
            select(RegulationClause).where(RegulationClause.id == _uuid(clause_id),
                                           RegulationClause.regulation_id == _uuid(reg_id))
        ).scalar_one_or_none()

    def sentence_count(self, reg_id: str | uuid.UUID) -> int:
        return int(self.db.execute(
            select(func.count()).select_from(RegulationSentence)
            .join(RegulationClause, RegulationClause.id == RegulationSentence.clause_id)
            .where(RegulationClause.regulation_id == _uuid(reg_id))
        ).scalar_one())

    # ── 임베딩 ─────────────────────────────────────────────────────────────
    def display_clause_count(self, reg_id: str | uuid.UUID) -> int:
        return int(self.db.execute(
            select(func.count()).select_from(RegulationClause)
            .where(RegulationClause.regulation_id == _uuid(reg_id), RegulationClause.display.is_(True))
        ).scalar_one())

    def pending_embeddings(self, reg_id: str | uuid.UUID, limit: int) -> list[tuple[str, uuid.UUID, str]]:
        """아직 임베딩이 없는 표시 대상 (종류, id, 글자) 최대 limit 건. 조항 먼저, 그다음 문장.

        조항 임베딩에는 머리글 정보를 붙인 글자를 쓴다(splitter.Clause.embed_text 와 같은 모양).
        """
        rid = _uuid(reg_id)
        out: list[tuple[str, uuid.UUID, str]] = []
        clauses = self.db.execute(
            select(RegulationClause).where(RegulationClause.regulation_id == rid,
                                           RegulationClause.display.is_(True),
                                           RegulationClause.embedding.is_(None))
            .order_by(RegulationClause.seq).limit(limit)
        ).scalars().all()
        for c in clauses:
            out.append(("clause", c.id, compose_embed_text(c.article_no, c.title, c.chapter, c.text_)))
        remaining = limit - len(out)
        if remaining > 0:
            sents = self.db.execute(
                select(RegulationSentence).join(RegulationClause, RegulationClause.id == RegulationSentence.clause_id)
                .where(RegulationClause.regulation_id == rid, RegulationClause.display.is_(True),
                       RegulationSentence.embedding.is_(None), RegulationSentence.is_lead.is_(False))
                .order_by(RegulationClause.seq, RegulationSentence.seq).limit(remaining)
            ).scalars().all()
            out.extend(("sentence", s.id, s.text_) for s in sents)
        return out

    def set_embeddings(self, items: Sequence[tuple[str, uuid.UUID, bytes]]) -> None:
        clause_rows = [{"id": i, "embedding": b} for k, i, b in items if k == "clause"]
        sent_rows = [{"id": i, "embedding": b} for k, i, b in items if k == "sentence"]
        if clause_rows:
            self.db.execute(update(RegulationClause), clause_rows)
        if sent_rows:
            self.db.execute(update(RegulationSentence), sent_rows)
        self.db.flush()

    def serving_rows(self, reg_id: str | uuid.UUID) -> list[tuple[RegulationClause, list[RegulationSentence]]]:
        """조회 색인용 — 표시 대상이고 임베딩이 있는 조항과 그 문장들(순번 순)."""
        rid = _uuid(reg_id)
        clauses = self.db.execute(
            select(RegulationClause).where(RegulationClause.regulation_id == rid,
                                           RegulationClause.display.is_(True),
                                           RegulationClause.embedding.is_not(None))
            .order_by(RegulationClause.seq)
        ).scalars().all()
        if not clauses:
            return []
        sents = self.db.execute(
            select(RegulationSentence).where(RegulationSentence.clause_id.in_([c.id for c in clauses]))
            .order_by(RegulationSentence.clause_id, RegulationSentence.seq)
        ).scalars().all()
        by_clause: dict[uuid.UUID, list[RegulationSentence]] = {}
        for s in sents:
            by_clause.setdefault(s.clause_id, []).append(s)
        return [(c, by_clause.get(c.id, [])) for c in clauses]

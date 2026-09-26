"""규정 참고 표시 — 등록·색인·활성화·보관·삭제·조항 수정 서비스 (설계서 §2.4).

⛔ 이 서비스는 **등급을 바꾸지 않는다.** 분류·검수 라우팅 코드를 부르지 않는다.

■ 흐름
    register  검증 → 파일 해시 중복 → 원본 저장(암호화 버킷) → 판 행(indexing) → 큐 발사(커밋 뒤)
    index     (워커) 원본 로드 → 추출(FUN-022) → 조항·문장 분할 → 종류 태깅 → 표시 대상 임베딩(배치·진행 저장) → ready
    activate  적용 대상 확인 필수 → 같은 규정명의 이전 활성 판을 같은 트랜잭션에서 보관
    delete    보관·실패·ready 판만. 조항·문장을 물리 삭제하고 원본 파일을 지운다

■ 실패는 상태로 남긴다
    서비스 예외를 삼켜 성공처럼 돌려주지 않는다(문서 벡터 색인의 "조용히 건너뜀" 관례와 다르다 — 규정 등록은
    사용자가 결과를 기다리는 동작이다). 결정형 실패(조항 없음·문장 상한·임베더 없음)는 판을 failed 로 바꾸고,
    일시 실패(임베더 로드 등)는 예외를 올려 Celery 재시도에 맡긴다(마지막 재시도에서 태스크가 mark_failed).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from koipa.regulation import status as st
from koipa.regulation import vectors
from koipa.regulation.splitter import compose_embed_text, split_regulation
from koipa.regulation.tagger import KINDS, SOURCE_ADMIN, SOURCE_AUTO, default_display, tag_clause

logger = logging.getLogger(__name__)

RAW_BUCKET = "regulations-raw"
ALLOWED_EXTENSIONS = ("txt", "md", "docx", "pdf", "hwp", "hwpx")
EMBED_BATCH = 32
NAME_MAX = 200
VERSION_MAX = 50
SCOPE_NOTE_MAX = 500
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RegulationError(Exception):
    """라우터가 HTTP 상태로 옮기는 도메인 예외(KeywordAdminError 와 같은 관례). 오류는 HTTP 상태로만 표현한다."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _IndexFailure(Exception):
    """되풀이해도 소용없는 색인 실패(조항 없음·상한 초과·임베더 없음) — 판을 failed 로 바꾼다."""


@dataclass(frozen=True)
class RegisterResult:
    reg_id: str
    status: str
    duplicate: bool


def _safe_name(filename: str, fallback: str) -> str:
    """디렉터리 성분을 뗀 안전한 basename(경로 탈출 방지). 비거나 '.'/'..' 이면 fallback."""
    base = re.split(r"[\\/]", filename or "")[-1]
    base = Path(base).name.strip()
    return fallback if base in {"", ".", ".."} else base


def _raw_key(file_hash: str, filename: str) -> str:
    # 행에는 이름을 500자로 잘라 적는다 — 저장 키도 같은 500자여야 읽기·삭제가 같은 키를 찾는다(501자 이상이면 색인이 영구 실패하고 원본이 남았다 — 독립 리뷰 R1)
    return f"{file_hash}/{_safe_name(filename, file_hash)[:500]}"


class RegulationService:
    _instance: "RegulationService | None" = None

    def __init__(
        self,
        *,
        storage: Any | None = None,
        embedder_factory: Callable[[], Any] | None = None,
        dispatcher: Callable[[str], None] | None = None,
        session_factory: Callable[[], AbstractContextManager] | None = None,
        settings_obj: Any | None = None,
    ) -> None:
        self._storage = storage
        self._embedder_factory = embedder_factory
        self._dispatcher = dispatcher
        self._session_factory = session_factory
        self._settings_obj = settings_obj

    # ── 싱글턴(라우터용) ────────────────────────────────────────────────────
    @classmethod
    def get_instance(cls) -> "RegulationService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_singleton(cls) -> None:
        cls._instance = None

    # ── 의존성(지연 생성 — 시험에서 주입) ─────────────────────────────────────
    def _settings(self) -> Any:
        if self._settings_obj is not None:
            return self._settings_obj
        from koipa.config import settings  # noqa: PLC0415
        return settings

    def _session(self) -> AbstractContextManager:
        if self._session_factory is not None:
            return self._session_factory()
        from koipa.db import session_scope  # noqa: PLC0415
        return session_scope()

    def _storage_obj(self) -> Any:
        if self._storage is None:
            from koipa.adapters.storage import build_storage  # noqa: PLC0415
            self._storage = build_storage()
        return self._storage

    def _real_embedder(self) -> Any:
        """운영 임베더. **해시 임베딩은 의미가 없으므로 쓰지 않는다**(프로파일이 hash 이거나 로드 실패로 폴백한 경우)."""
        embedder = self._embedder_factory() if self._embedder_factory else self._default_embedder()
        if getattr(embedder, "name", "") == "hash" or getattr(embedder, "_underlying_name", "") == "hash":
            raise _IndexFailure("실제 임베더가 필요합니다. 임베딩 제공자가 hash 이거나 모델을 불러오지 못했습니다.")
        return embedder

    @staticmethod
    def _default_embedder() -> Any:
        from koipa.adapters.embedding import HashEmbedding, build_embedder  # noqa: PLC0415
        emb = build_embedder()
        if isinstance(emb, HashEmbedding):
            raise _IndexFailure("실제 임베더가 필요합니다. 임베딩 제공자가 hash 이거나 모델을 불러오지 못했습니다.")
        return emb

    def _dispatch(self, reg_id: str) -> None:
        if self._dispatcher is not None:
            self._dispatcher(reg_id)
            return
        from koipa.workers.tasks import index_regulation  # noqa: PLC0415
        index_regulation.delay(reg_id)

    def _queue_available(self) -> bool:
        if self._dispatcher is not None:
            return True
        from koipa.services.async_classify_service import _celery_dispatch_available  # noqa: PLC0415
        return _celery_dispatch_available()

    @staticmethod
    def _invalidate_cache() -> None:
        """활성 규정이 바뀌었다 — 이 프로세스의 조회 캐시를 버린다(다른 프로세스는 TTL 로 수렴)."""
        from koipa.services.regulation_evidence_service import RegulationEvidenceService  # noqa: PLC0415
        RegulationEvidenceService.get_instance().invalidate()

    @staticmethod
    def _audit(db: Any, action: str, reg: Any, actor_id: str | None, actor_role: str | None, payload: dict | None = None) -> None:
        from koipa.repositories.audit_repo import AuditRepo  # noqa: PLC0415
        AuditRepo(db).record(action=action, actor_id=actor_id, actor_role=actor_role, target_type="regulation",
                             target_id=str(reg.id), payload={"name": reg.name, "version": reg.version_label, **(payload or {})})

    # ── 등록 ──────────────────────────────────────────────────────────────
    def register(self, *, data: bytes, filename: str, name: str, version_label: str, effective_date: str | None,
                 actor_id: str | None, actor_role: str | None) -> RegisterResult:
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415

        name, version_label = (name or "").strip(), (version_label or "").strip()
        if not name or len(name) > NAME_MAX:
            raise RegulationError(422, f"규정명은 1~{NAME_MAX}자여야 합니다")
        if not version_label or len(version_label) > VERSION_MAX:
            raise RegulationError(422, f"판 표기는 1~{VERSION_MAX}자여야 합니다")
        if effective_date and not _ISO_DATE.match(effective_date):
            raise RegulationError(422, "시행일은 YYYY-MM-DD 형식이어야 합니다")
        ext = Path(filename or "").suffix.lower().lstrip(".")
        if ext not in ALLOWED_EXTENSIONS:
            raise RegulationError(422, f"지원하지 않는 형식입니다: .{ext} (지원: {', '.join(ALLOWED_EXTENSIONS)})")
        if not data:
            raise RegulationError(422, "빈 파일입니다")
        if len(data) > int(self._settings().max_upload_mb) * 1024 * 1024:
            raise RegulationError(413, "file too large")

        file_hash = hashlib.sha256(data).hexdigest()
        with self._session() as db:
            repo = RegulationRepo(db)
            existing = repo.find_live_by_hash(file_hash)
            if existing is not None:
                return RegisterResult(str(existing.id), existing.status, True)
            if not self._queue_available():
                raise RegulationError(503, "색인 작업 큐를 사용할 수 없습니다")   # 부작용(파일 저장·행 생성) 전에 거절
            raw_uri = self._storage_obj().put(RAW_BUCKET, _raw_key(file_hash, filename), data)
            row = repo.create(name=name, version_label=version_label, file_hash=file_hash,
                              filename=_safe_name(filename, file_hash)[:500], source_format=ext, raw_uri=raw_uri,
                              effective_date=effective_date or None, created_by=actor_id)
            self._audit(db, "regulation.upload", row, actor_id, actor_role, {"sha256": file_hash, "bytes": len(data)})
            reg_id = str(row.id)
        self._dispatch(reg_id)          # 커밋 뒤 — 워커가 판 행을 볼 수 있어야 한다
        return RegisterResult(reg_id, st.INDEXING, False)

    # ── 색인(워커) ─────────────────────────────────────────────────────────
    def index(self, reg_id: str) -> dict:
        """원본 → 조항·문장 → 임베딩. 결정형 실패는 failed 로 남기고, 일시 실패는 예외를 올린다."""
        try:
            return self._index(reg_id)
        except _IndexFailure as exc:
            self.mark_failed(reg_id, str(exc))
            return {"status": st.FAILED, "error": str(exc)}

    def _index(self, reg_id: str) -> dict:
        from koipa.modules.m2_preprocess.extractor import extract  # noqa: PLC0415
        from koipa.repositories.regulation_repo import ClausePlan, RegulationRepo  # noqa: PLC0415

        with self._session() as db:
            repo = RegulationRepo(db)
            reg = repo.get(reg_id)
            if reg is None:
                return {"status": "missing"}
            if reg.status != st.INDEXING:
                return {"status": reg.status, "skipped": True}
            already_built = reg.clause_count > 0          # 재시도 — 조항·문장은 이미 저장돼 있다
            file_hash, filename, ext = reg.file_hash, reg.filename, reg.source_format

        if not already_built:
            try:
                data = self._storage_obj().get(RAW_BUCKET, _raw_key(file_hash, filename))
            except Exception as exc:  # noqa: BLE001
                raise _IndexFailure(f"원본 파일을 읽지 못했습니다: {exc}") from exc
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / f"regulation.{ext}"
                path.write_bytes(data)
                ext_res = extract(path)
            if ext_res.error or not (ext_res.text or "").strip():
                raise _IndexFailure(f"본문을 추출하지 못했습니다: {ext_res.error or '내용이 비어 있습니다'}")
            split = split_regulation(ext_res.text)
            if not split.clauses:
                raise _IndexFailure("조항을 찾지 못했습니다. 규정 형식(제N조 또는 번호 제목)을 확인해 주십시오.")
            plans = []
            for c in split.clauses:
                kind = tag_clause(c.title, c.chapter)
                plans.append(ClausePlan(c, kind, SOURCE_AUTO, default_display(kind)))
            n_clause = sum(1 for p in plans if p.display)
            n_sent = sum(1 for p in plans if p.display for s in p.clause.sentences if not s.is_lead)
            if n_sent > int(self._settings().regulation_max_sentences):
                raise _IndexFailure(f"표시 대상 문장이 {n_sent}개로 상한({self._settings().regulation_max_sentences})을 "
                                    "넘었습니다. 규정을 나누어 올려 주십시오.")
            warnings = list(split.warnings)
            if getattr(ext_res, "table_coverage", None) == "incomplete":
                warnings.append("표 안의 글자가 빠졌을 수 있습니다.")
            if n_clause == 0:
                warnings.append("표시 대상 조항이 없습니다. 조항 종류를 확인해 주십시오.")
            with self._session() as db:
                repo = RegulationRepo(db)
                reg = repo.get(reg_id)
                repo.replace_clauses(reg.id, plans)
                reg.split_mode = split.mode
                reg.clause_count = len(plans)
                reg.sentence_count = sum(len(p.clause.sentences) for p in plans)
                reg.embed_target_count = n_clause + n_sent
                reg.embedded_count = 0
                reg.warnings_text = "\n".join(warnings) or None

        embedder = self._real_embedder()
        model: str | None = None
        while True:
            with self._session() as db:
                repo = RegulationRepo(db)
                batch = repo.pending_embeddings(reg_id, EMBED_BATCH)
                if not batch:
                    break
                result = embedder.embed([t for _, _, t in batch])
                model = result.model
                repo.set_embeddings([(k, i, vectors.encode(vectors.normalize(v)))
                                     for (k, i, _), v in zip(batch, result.vectors)])
                reg = repo.get(reg_id)
                reg.embedded_count = (reg.embedded_count or 0) + len(batch)
                reg.embed_model = model or reg.embed_model

        with self._session() as db:
            repo = RegulationRepo(db)
            reg = repo.get(reg_id)
            reg.status, reg.error_message = st.READY, None
            if model:
                reg.embed_model = model
            reg.updated_at = dt.datetime.now(dt.timezone.utc)
            self._audit(db, "regulation.indexed", reg, None, "system",
                        {"clauses": reg.clause_count, "sentences": reg.sentence_count})
        return {"status": st.READY}

    def mark_failed(self, reg_id: str, message: str) -> None:
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415
        with self._session() as db:
            reg = RegulationRepo(db).get(reg_id)
            if reg is None or reg.status != st.INDEXING:
                return
            reg.status, reg.error_message = st.FAILED, (message or "")[:1000]
            reg.updated_at = dt.datetime.now(dt.timezone.utc)
            self._audit(db, "regulation.index_failed", reg, None, "system", {"error": (message or "")[:200]})
        logger.warning("규정 색인 실패: reg_id=%s %s", reg_id, message)

    # ── 조회 ──────────────────────────────────────────────────────────────
    def get(self, reg_id: str) -> dict:
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415
        with self._session() as db:
            repo = RegulationRepo(db)
            reg = repo.get(reg_id)
            if reg is None:
                raise RegulationError(404, "regulation not found")
            return self._detail(repo, reg)

    def list(self, *, status: str | None, limit: int, offset: int) -> dict:
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415
        if status and status not in st.ALL:
            raise RegulationError(422, f"알 수 없는 상태입니다: {status}")
        with self._session() as db:
            repo = RegulationRepo(db)
            rows, total = repo.list(status=status, limit=limit, offset=offset)
            return {"items": [self._summary(r) for r in rows], "total": total}

    def list_clauses(self, reg_id: str, *, kind: str | None, display: bool | None, limit: int, offset: int) -> dict:
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415
        if kind and kind not in KINDS:
            raise RegulationError(422, f"알 수 없는 종류입니다: {kind}")
        with self._session() as db:
            repo = RegulationRepo(db)
            if repo.get(reg_id) is None:
                raise RegulationError(404, "regulation not found")
            rows, total = repo.list_clauses(reg_id, kind=kind, display=display, limit=limit, offset=offset)
            return {"items": [self._clause(c) for c in rows], "total": total}

    @staticmethod
    def _summary(r: Any) -> dict:
        return {"reg_id": str(r.id), "name": r.name, "version_label": r.version_label, "status": r.status,
                "clause_count": r.clause_count, "sentence_count": r.sentence_count,
                "effective_date": r.effective_date, "created_at": r.created_at.isoformat() if r.created_at else None,
                "activated_at": r.activated_at.isoformat() if r.activated_at else None}

    def _detail(self, repo: Any, r: Any) -> dict:
        out = self._summary(r)
        out.update({
            "filename": r.filename, "source_format": r.source_format, "split_mode": r.split_mode,
            "embed_model": r.embed_model, "embed_target_count": r.embed_target_count,
            "embedded_count": r.embedded_count, "scope_note": r.scope_note, "scope_confirmed": r.scope_confirmed,
            "warnings": [w for w in (r.warnings_text or "").split("\n") if w], "error_message": r.error_message,
            "display_clause_count": repo.display_clause_count(r.id),
        })
        return out

    @staticmethod
    def _clause(c: Any) -> dict:
        return {"clause_id": str(c.id), "seq": c.seq, "article_no": c.article_no, "title": c.title,
                "chapter": c.chapter, "kind": c.kind, "kind_source": c.kind_source, "display": c.display,
                "text": c.text_}

    # ── 상태 변경 ──────────────────────────────────────────────────────────
    def activate(self, reg_id: str, *, scope_confirmed: bool, scope_note: str | None,
                 actor_id: str | None, actor_role: str | None) -> dict:
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415
        if not scope_confirmed:
            raise RegulationError(422, "적용 대상 확인이 필요합니다")
        scope_note = (scope_note or "").strip()[:SCOPE_NOTE_MAX] or None
        with self._session() as db:
            repo = RegulationRepo(db)
            reg = repo.get(reg_id)
            if reg is None:
                raise RegulationError(404, "regulation not found")
            if not st.can_activate(reg.status):
                raise RegulationError(409, f"활성화할 수 없는 상태입니다: {st.label(reg.status)}")
            if not repo.serving_rows(reg.id):
                raise RegulationError(409, "표시할 수 있는 조항이 없습니다")
            is_replacement = any(a.name == reg.name and a.id != reg.id for a in repo.active())
            if not is_replacement and repo.count_active_names(exclude_id=reg.id) >= int(self._settings().regulation_active_max):
                raise RegulationError(409, "동시에 사용할 수 있는 규정 수를 넘었습니다")
            archived = repo.activate(reg, scope_note=scope_note)
            self._audit(db, "regulation.activate", reg, actor_id, actor_role,
                        {"archived": [str(a.id) for a in archived], "scope_note": bool(scope_note)})
            out = self._detail(repo, reg)
        self._invalidate_cache()
        return out

    def archive(self, reg_id: str, *, actor_id: str | None, actor_role: str | None) -> dict:
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415
        with self._session() as db:
            repo = RegulationRepo(db)
            reg = repo.get(reg_id)
            if reg is None:
                raise RegulationError(404, "regulation not found")
            if reg.status != st.ACTIVE:
                raise RegulationError(409, f"보관할 수 없는 상태입니다: {st.label(reg.status)}")
            repo.archive(reg)
            self._audit(db, "regulation.archive", reg, actor_id, actor_role)
            out = self._detail(repo, reg)
        self._invalidate_cache()
        return out

    def delete(self, reg_id: str, *, actor_id: str | None, actor_role: str | None) -> None:
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415
        with self._session() as db:
            repo = RegulationRepo(db)
            reg = repo.get(reg_id)
            if reg is None:
                raise RegulationError(404, "regulation not found")
            if not st.can_delete(reg.status):
                raise RegulationError(409, f"삭제할 수 없는 상태입니다: {st.label(reg.status)}")
            key = _raw_key(reg.file_hash, reg.filename)
            repo.soft_delete(reg)
            self._audit(db, "regulation.delete", reg, actor_id, actor_role)
        try:
            self._storage_obj().delete(RAW_BUCKET, key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("규정 원본 삭제 실패(행은 지웠다): %s", exc)
        self._invalidate_cache()

    def update_clause(self, reg_id: str, clause_id: str, *, display: bool | None, kind: str | None,
                      actor_id: str | None, actor_role: str | None) -> dict:
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415
        if display is None and kind is None:
            raise RegulationError(422, "display 또는 kind 가 필요합니다")
        if kind is not None and kind not in KINDS:
            raise RegulationError(422, f"알 수 없는 종류입니다: {kind}")
        was_active = False
        with self._session() as db:
            repo = RegulationRepo(db)
            reg = repo.get(reg_id)
            if reg is None:
                raise RegulationError(404, "regulation not found")
            if not st.can_edit(reg.status):
                raise RegulationError(409, f"수정할 수 없는 상태입니다: {st.label(reg.status)}")
            clause = repo.get_clause(reg_id, clause_id)
            if clause is None:
                raise RegulationError(404, "clause not found")
            before = {"display": clause.display, "kind": clause.kind}
            if kind is not None and kind != clause.kind:
                clause.kind, clause.kind_source = kind, SOURCE_ADMIN
            if display is not None and display != clause.display:
                clause.display, clause.kind_source = display, SOURCE_ADMIN
                if display and clause.embedding is None:
                    self._embed_clause_now(repo, clause)      # 새로 표시 대상이 된 조항은 지금 임베딩한다
            reg.updated_at = dt.datetime.now(dt.timezone.utc)
            was_active = reg.status == st.ACTIVE
            self._audit(db, "regulation.clause_update", reg, actor_id, actor_role,
                        {"clause": clause.article_no, "before": before, "after": {"display": clause.display, "kind": clause.kind}})
            out = self._clause(clause)
        if was_active:
            self._invalidate_cache()
        return out

    def _embed_clause_now(self, repo: Any, clause: Any) -> None:
        """표시 대상이 된 조항(+문장)을 요청 안에서 임베딩한다 — 조항 하나(문장 수십 개 이하)라 작다."""
        from koipa.db.models import RegulationSentence  # noqa: PLC0415
        from sqlalchemy import select  # noqa: PLC0415
        try:
            embedder = self._real_embedder()
        except _IndexFailure as exc:
            raise RegulationError(503, str(exc)) from exc
        sents = repo.db.execute(select(RegulationSentence).where(
            RegulationSentence.clause_id == clause.id, RegulationSentence.is_lead.is_(False),
            RegulationSentence.embedding.is_(None)).order_by(RegulationSentence.seq)).scalars().all()
        texts = [compose_embed_text(clause.article_no, clause.title, clause.chapter, clause.text_)]
        texts += [s.text_ for s in sents]
        result = embedder.embed(texts)
        clause.embedding = vectors.encode(vectors.normalize(result.vectors[0]))
        for s, v in zip(sents, result.vectors[1:]):
            s.embedding = vectors.encode(vectors.normalize(v))


def get_regulation_service() -> RegulationService:
    return RegulationService.get_instance()

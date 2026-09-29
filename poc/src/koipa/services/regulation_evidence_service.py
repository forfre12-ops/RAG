"""문서별 관련 규정 조회 — 검수 화면의 「관련 규정(참고)」 (설계서 §2.5).

⛔ 등급을 바꾸지 않는다. 점수를 돌려주지 않는다. 기본은 결정형이다(LLM·랜덤 없음).
   선택 옵션 regulation_llm_select_enabled 를 켜면 조회가 준 후보 조항을 **로컬 LLM** 이 훑어 문서에 직접 적용되는 항을 고른다 —
   화면에는 그 항의 규정 원문만 나가고, 해당 항이 없으면 아무것도 안 보인다(설계서 §3.6).

■ 입력
    문서 대표 벡터  DocumentVectorStore.get(doc_id) — 이미 색인돼 있다(청크 임베딩 평균, 유사 문서 조회용)
    문서 본문 앞부분  청크 텍스트 ≤ 6,000자 — 낱말 채널 질의(LLM 옵션이 켜지면 프롬프트에도 앞 1,500자가 들어간다)

■ 비는 경우(모두 오류가 아니라 정상 응답, reason 을 싣는다)
    no_active_regulation · document_not_indexed · embedder_mismatch · below_floor · no_sentence
    LLM 옵션이 켜졌을 때: not_applicable(모든 후보에서 해당 항 없음) · public_document(이미 공개된 외부 자료라 사내 규정을 안 붙임)
                          · llm_unavailable(LLM 오류·시간 초과·해석 불가) · llm_not_local(공급자가 로컬이 아니거나 서버 주소가 사내가 아니라 호출하지 않음)

■ 캐시
    활성 규정의 조항·문장 벡터와 낱말 색인을 프로세스 메모리에 올린다. 활성화·보관·삭제·표시 토글이 일어난
    프로세스는 즉시 버리고(`invalidate`), 다른 프로세스는 TTL(30초)마다 활성 판의 지문(id·수정 시각)을 보고
    바뀌었을 때만 다시 만든다.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import numpy as np

if TYPE_CHECKING:
    from koipa.schemas.regulation import EvidenceItemModel

from koipa.regulation import llm_select, vectors
from koipa.regulation.index import (
    REASON_NO_CLAUSES,
    ClauseRec,
    EvidenceItem,
    RegulationIndex,
    SentenceRec,
)
from koipa.regulation.splitter import compose_embed_text

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30
DOC_TEXT_MAX_CHARS = 6000
LLM_CACHE_MAX_ENTRIES = 2000

REASON_DOC_NOT_INDEXED = "document_not_indexed"
REASON_EMBEDDER_MISMATCH = "embedder_mismatch"
REASON_DISABLED_BY_ADMIN = "disabled_by_admin"


@dataclass
class EvidenceResult:
    doc_id: str
    indexed: bool | None                 # None = 문서 벡터를 확인하지 않았다(활성 규정이 없어서)
    reason: str | None = None
    items: list[EvidenceItem] = field(default_factory=list)


def _clause_rec(reg: Any, clause: Any, sentences: list[Any]) -> ClauseRec:
    return ClauseRec(
        clause_id=str(clause.id), rgltn_id=str(reg.id), rgltn_nm=reg.name, ver_lbl_nm=reg.version_label,
        seq=clause.seq, article_no=clause.article_no, title=clause.title,
        text=compose_embed_text(clause.article_no, clause.title, clause.chapter, clause.text_),
        vec=vectors.normalize(vectors.decode(clause.embedding)),
        sentences=tuple(
            SentenceRec(str(s.id), s.seq, s.text_, bool(s.is_lead), s.list_group,
                        vectors.normalize(vectors.decode(s.embedding)) if s.embedding else None)
            for s in sentences
        ),
    )


class RegulationEvidenceService:
    _instance: "RegulationEvidenceService | None" = None

    def __init__(
        self,
        *,
        vector_store: Any | None = None,
        session_factory: Callable[[], AbstractContextManager] | None = None,
        settings_obj: Any | None = None,
        doc_text_provider: Callable[[str], str] | None = None,
        document_exists: Callable[[str], bool] | None = None,
        llm_provider_factory: Callable[[], Any] | None = None,
        ttl_seconds: float = CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._vector_store = vector_store
        self._session_factory = session_factory
        self._settings_obj = settings_obj
        self._doc_text_provider = doc_text_provider
        self._document_exists = document_exists
        self._llm_provider_factory = llm_provider_factory
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._groups: dict[str, RegulationIndex] | None = None
        self._signature: tuple[int, str] | None = None
        self._checked_at = 0.0
        # LLM 판정 결과 캐시 — 키에 규정 판 지문·모델·후보 수·문서 글자 지문이 들어 있어 무엇이 바뀌어도 자연히 갈린다
        self._llm_cache: "OrderedDict[tuple, tuple[float, list[EvidenceItem], str | None]]" = OrderedDict()
        self._llm_cache_lock = threading.Lock()
        self._llm_provider: Any | None = None

    # ── 싱글턴 ────────────────────────────────────────────────────────────
    @classmethod
    def get_instance(cls) -> "RegulationEvidenceService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_singleton(cls) -> None:
        cls._instance = None

    # ── 의존성 ────────────────────────────────────────────────────────────
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

    def _store(self) -> Any:
        if self._vector_store is None:
            from koipa.adapters.vectorstore.document_vectors import DocumentVectorStore  # noqa: PLC0415
            self._vector_store = DocumentVectorStore()
        return self._vector_store

    def document_exists(self, doc_id: str) -> bool:
        """삭제하지 않은 문서가 있는가 — "문서 없음"(404)과 "아직 색인 전"(정상 응답)을 가른다."""
        if self._document_exists is not None:
            return self._document_exists(doc_id)
        from sqlalchemy import select  # noqa: PLC0415
        from koipa.db.models import Document  # noqa: PLC0415
        with self._session() as db:
            return db.execute(select(Document.doc_id).where(Document.doc_id == doc_id, Document.deleted_at.is_(None))
                              ).first() is not None

    def _doc_text(self, doc_id: str) -> str:
        if self._doc_text_provider is not None:
            return self._doc_text_provider(doc_id)
        from koipa.repositories.chunk_repo import ChunkRepo  # noqa: PLC0415
        parts: list[str] = []
        size = 0
        with self._session() as db:
            for ch in ChunkRepo(db).get_by_doc_id(doc_id, limit=30):
                parts.append(ch.content)
                size += len(ch.content)
                if size >= DOC_TEXT_MAX_CHARS:
                    break
        return "\n".join(parts)[:DOC_TEXT_MAX_CHARS]

    # ── 캐시 ──────────────────────────────────────────────────────────────
    def invalidate(self) -> None:
        with self._lock:
            self._groups = None
            self._signature = None
            self._checked_at = 0.0
        with self._llm_cache_lock:
            self._llm_cache.clear()

    def _build_groups(self, repo: Any, regs: list[Any]) -> dict[str, RegulationIndex]:
        by_model: dict[str, list[ClauseRec]] = {}
        for reg in regs:
            if not reg.embed_model:
                continue
            rows = repo.serving_rows(reg.id)
            by_model.setdefault(reg.embed_model, []).extend(_clause_rec(reg, c, sents) for c, sents in rows)
        return {m: RegulationIndex(cs, m) for m, cs in by_model.items() if cs}

    def _active_groups(self) -> dict[str, RegulationIndex]:
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415
        now = self._clock()
        with self._lock:
            if self._groups is not None and now - self._checked_at < self._ttl:
                return self._groups
            known = self._signature
        with self._session() as db:
            repo = RegulationRepo(db)
            sig = repo.active_signature()
            with self._lock:
                if self._groups is not None and known == sig:      # 지문이 같으면 다시 만들지 않는다
                    self._checked_at = now
                    return self._groups
            groups = self._build_groups(repo, repo.active()) if sig[0] else {}
        with self._lock:
            self._groups, self._signature, self._checked_at = groups, sig, now
        return groups

    # ── 조회 ──────────────────────────────────────────────────────────────
    def find_for_document(self, doc_id: str, *, max_items: int | None = None) -> EvidenceResult:
        """문서가 없으면 LookupError(→ 404). 활성 규정이 없으면 문서를 확인하지 않고 바로 비운다.

        검수 화면 전용 진입점이라 여기서만 런타임 스위치(콘솔 체크박스, 재시작 불필요)를 본다 —
        관리자의 「미리보기」(preview)는 이 스위치와 무관하게 항상 동작해야 끄기 전에 확인할 수 있다.
        """
        from koipa.regulation import runtime_toggle  # noqa: PLC0415

        if not runtime_toggle.is_enabled():
            return EvidenceResult(doc_id, None, REASON_DISABLED_BY_ADMIN, [])
        groups = self._active_groups()
        if not groups:
            return EvidenceResult(doc_id, None, REASON_NO_CLAUSES, [])
        return self._find(doc_id, groups, max_items, missing_is_error=True)

    def _find(self, doc_id: str, groups: dict[str, RegulationIndex], max_items: int | None,
              *, missing_is_error: bool = False, use_cache: bool = True) -> EvidenceResult:
        s = self._settings()
        dv = self._store().get(doc_id)
        if dv is None:
            if missing_is_error and not self.document_exists(doc_id):
                raise LookupError("document not found")
            return EvidenceResult(doc_id, False, REASON_DOC_NOT_INDEXED, [])
        index = groups.get(dv.model)
        if index is None:
            return EvidenceResult(doc_id, True, REASON_EMBEDDER_MISMATCH, [])
        limit = max(1, min(3, int(max_items if max_items is not None else s.regulation_evidence_max_items)))
        doc_vec = vectors.normalize(np.asarray(dv.embedding, dtype=np.float32))
        doc_text = self._doc_text(doc_id)
        from koipa.regulation import runtime_toggle  # noqa: PLC0415
        if runtime_toggle.is_llm_select_enabled(default=bool(getattr(s, "regulation_llm_select_enabled", False))):
            return self._find_with_llm(doc_id, index, doc_vec, doc_text, limit, s, use_cache=use_cache)
        res = index.find(doc_vec, doc_text, max_items=limit, floor=float(s.regulation_min_similarity))
        return EvidenceResult(doc_id, True, res.reason, res.items)

    # ── 로컬 LLM 으로 「해당되는 항」 고르기 ─────────────────────────────────────
    def _llm(self) -> Any:
        if self._llm_provider is None:
            if self._llm_provider_factory is not None:
                provider = self._llm_provider_factory()
            else:
                from koipa.adapters.llm import build_provider  # noqa: PLC0415
                provider = build_provider()
            # 사람이 화면에서 기다리는 호출이라 OpenAI SDK 기본(시간 초과 600초·재시도 2회)과 어댑터 재시도(3회)를 끈다 — 서버가 응답을 안 하면 호출 한 건이
            # 최악 약 7,200초 동안 공유 실행기의 스레드를 붙잡아 이후 요청이 전부 시간 초과가 된다(독립 리뷰 R2). 호출 하나의 한도 = 문서 한 건의 마감.
            limit = getattr(provider, "limit_calls", None)
            if callable(limit):
                limit(timeout_s=float(self._settings().regulation_llm_timeout_s))
            self._llm_provider = provider
        return self._llm_provider

    def _find_with_llm(self, doc_id: str, index: RegulationIndex, doc_vec: np.ndarray, doc_text: str, limit: int, s: Any,
                       *, use_cache: bool) -> EvidenceResult:
        """조회 순위 상위 후보 조항을 로컬 LLM 에게 물어 문서에 직접 적용되는 항만 보인다. 아니면 아무것도 안 보인다.

        ⛔ 로컬이 아닌 공급자(원격·목업)이면 호출하지 않는다 — 문서 본문이 프롬프트에 들어간다. 판정이 서지 않아도(오류·시간 초과) 안 보인다.
        """
        provider_name = str(getattr(s, "llm_provider", "") or "")
        if not llm_select.is_local_provider(provider_name):
            logger.error("규정 LLM 판정을 건너뛴다 — 공급자 %r 는 로컬이 아니다(문서 본문을 밖으로 보내지 않는다)", provider_name)
            return EvidenceResult(doc_id, True, llm_select.REASON_LLM_NOT_LOCAL, [])
        k = int(s.regulation_llm_candidates)
        doc_chars = int(s.regulation_llm_doc_chars)
        ttl = float(getattr(s, "regulation_llm_cache_ttl_s", 0))
        try:
            provider = self._llm()
        except Exception as exc:  # noqa: BLE001 — 공급자를 못 만들면 판정이 안 선다
            logger.warning("규정 LLM 공급자 생성 실패: %s", type(exc).__name__)
            return EvidenceResult(doc_id, True, llm_select.REASON_LLM_UNAVAILABLE, [])
        # 이름이 로컬 공급자여도 vllm·local_openai 는 LOCAL_LLM_BASE_URL 을 그대로 쓴다 — 서버 **주소**가 사내가 아니면 문서 본문을 보내지 않는다(독립 리뷰 R1·R3).
        # 주소를 드러내지 않는 공급자(시험용 가짜)는 이 확인을 건너뛴다 — 운영 어댑터는 base_url 을 드러낸다.
        base_url = getattr(provider, "base_url", None)
        if base_url is not None and not llm_select.endpoint_is_local(base_url):
            logger.error("규정 LLM 판정을 건너뛴다 — LLM 서버 주소(호스트 %r)가 사내가 아니거나 확인되지 않는다(문서 본문을 밖으로 보내지 않는다)",
                         urlsplit(str(base_url)).hostname)
            return EvidenceResult(doc_id, True, llm_select.REASON_LLM_NOT_LOCAL, [])
        if not doc_text.strip():                # 본문이 비면 LLM 이 빈 [문서 앞부분] 으로 아무 항이나 고를 수 있다 — 묻지 않는다
            return EvidenceResult(doc_id, True, llm_select.REASON_NOT_APPLICABLE, [])
        skip_public = bool(getattr(s, "regulation_llm_skip_public", True))
        mode = str(getattr(s, "regulation_llm_mode", llm_select.MODE_PER_CANDIDATE))
        if mode not in llm_select.MODES:
            mode = llm_select.MODE_PER_CANDIDATE
        floor = float(s.regulation_min_similarity)
        # 키: 규정 판 지문·공급자·모델·후보 수·글자 수·개수·공개 확인·방식·문턱 + 조회가 실제로 보는 본문 전체(≤6,000자)와 문서 벡터의 지문.
        # (프롬프트에 들어가는 앞 1,500자만 지문으로 삼으면 뒷부분만 바뀐 문서가 옛 결과를 받는다 — 낱말 채널은 6,000자를 본다.)
        digest = hashlib.sha1(" ".join(doc_text.split()).encode("utf-8")).hexdigest()[:16]
        vec_digest = hashlib.sha1(np.ascontiguousarray(doc_vec, dtype=np.float32).tobytes()).hexdigest()[:16]
        key = (doc_id, self._signature, provider_name, str(getattr(provider, "model", "")), k, doc_chars, limit, skip_public, mode, floor, digest, vec_digest)
        if use_cache and ttl > 0:
            hit = self._llm_cache_get(key, ttl)
            if hit is not None:
                return EvidenceResult(doc_id, True, hit[1], list(hit[0]))
        candidates = index.rank_clauses(doc_vec, doc_text, k, floor=floor)
        sel = llm_select.select_applicable(
            provider, doc_text, candidates, limit=limit, doc_chars=doc_chars, timeout_s=float(s.regulation_llm_timeout_s),
            executor=llm_select.shared_executor(int(s.regulation_llm_max_concurrency)), skip_public=skip_public, mode=mode)
        if sel.failures:
            logger.warning("규정 LLM 판정 일부 실패(호출 %d건 중 %d건) — 결과를 기억하지 않는다(다음에 다시 시도)", sel.calls, sel.failures)
        # 실패는 기억하지 않는다 — 전부 실패했을 때뿐 아니라 **일부만** 실패했을 때도(1위 후보 호출이 일시 오류로 빠진 채 2위가 「가장 직접적인 항」으로 1시간 굳지 않게).
        if use_cache and ttl > 0 and sel.reason != llm_select.REASON_LLM_UNAVAILABLE and not sel.failures:
            self._llm_cache_put(key, sel.items, sel.reason)
        return EvidenceResult(doc_id, True, sel.reason, sel.items)

    def _llm_cache_get(self, key: tuple, ttl: float) -> tuple[list[EvidenceItem], str | None] | None:
        now = self._clock()
        with self._llm_cache_lock:
            hit = self._llm_cache.get(key)
            if hit is None:
                return None
            if now - hit[0] >= ttl:
                del self._llm_cache[key]
                return None
            self._llm_cache.move_to_end(key)
            return hit[1], hit[2]

    def _llm_cache_put(self, key: tuple, items: list[EvidenceItem], reason: str | None) -> None:
        with self._llm_cache_lock:
            self._llm_cache[key] = (self._clock(), list(items), reason)
            self._llm_cache.move_to_end(key)
            while len(self._llm_cache) > LLM_CACHE_MAX_ENTRIES:
                self._llm_cache.popitem(last=False)

    def preview(self, reg_id: str, doc_ids: list[str]) -> list[EvidenceResult]:
        """활성화 전 미리보기 — 후보 규정 **하나만**으로 문서마다 어떻게 보이는지(캐시를 쓰지 않는다)."""
        from koipa.repositories.regulation_repo import RegulationRepo  # noqa: PLC0415
        with self._session() as db:
            repo = RegulationRepo(db)
            reg = repo.get(reg_id)
            if reg is None:
                raise LookupError("regulation not found")
            groups = self._build_groups(repo, [reg]) if reg.embed_model else {}
        if not groups:
            return [EvidenceResult(d, None, REASON_NO_CLAUSES, []) for d in doc_ids]
        return self._find_many(doc_ids, groups)

    def _find_many(self, doc_ids: list[str], groups: dict[str, RegulationIndex]) -> list[EvidenceResult]:
        """미리보기용 — 문서마다 캐시 없이 판정한다. LLM 옵션이 켜져 있으면 문서를 순차로 판정하고 문서마다 마감이 따로라 한 요청이
        (문서 수 × 마감)까지 걸릴 수 있다 — 전체 예산(마감의 2배)을 두고 넘으면 남은 문서는 판정하지 않는다(독립 리뷰 R1·R2).
        GPU 에서는 20건이 약 1분이라 예산 안이다."""
        s = self._settings()
        budget = 2.0 * float(getattr(s, "regulation_llm_timeout_s", 60.0)) if getattr(s, "regulation_llm_select_enabled", False) else None
        started = self._clock()
        results: list[EvidenceResult] = []
        for d in doc_ids:
            if budget is not None and results and self._clock() - started > budget:
                results.append(EvidenceResult(d, None, llm_select.REASON_LLM_UNAVAILABLE, []))      # 예산을 넘겼다 — 문서를 확인하지 않았다(indexed=None)
                continue
            results.append(self._find(d, groups, None, use_cache=False))
        return results


def get_regulation_evidence_service() -> RegulationEvidenceService:
    return RegulationEvidenceService.get_instance()


def regulation_reference_for_kl_wire(doc_id: str) -> list[EvidenceItemModel] | None:
    """분류 결과에 실어 KL 로 보낼 규정참고 — 없으면(꺼짐·미색인·오류) None.

    [2026-09-29] async_classify.py(GET /classify/jobs/{job_id} kl_backend 분기)·
    workers/tasks.py(콜백 발사)가 부른다. `find_for_document` 는 원래 검수 화면 전용
    진입점이라 여기서 실패를 전부 삼킨다 — 규정참고 조회 실패로 분류 응답 자체가
    막히면 안 된다(등급 전달이 우선, 참고는 있으면 더하는 것).
    """
    from koipa.config import settings  # noqa: PLC0415
    from koipa.schemas.regulation import EvidenceClauseRef, EvidenceItemModel, EvidenceRegulationRef  # noqa: PLC0415

    if not getattr(settings, "regulation_reference_enabled", False):
        return None
    try:
        result = RegulationEvidenceService.get_instance().find_for_document(doc_id)
    except LookupError:
        return None
    except Exception:  # noqa: BLE001
        logger.warning("regulation_reference_for_kl_wire: 조회 실패 doc_id=%s", doc_id, exc_info=True)
        return None
    if not result.items:
        return None
    return [
        EvidenceItemModel(
            regulation=EvidenceRegulationRef(reg_id=i.rgltn_id, name=i.rgltn_nm, version_label=i.ver_lbl_nm),
            clause=EvidenceClauseRef(clause_id=i.clause_id, article_no=i.article_no, title=i.title),
            sentences=list(i.sentences),
            is_grade_list=i.is_grade_list,
        )
        for i in result.items
    ]

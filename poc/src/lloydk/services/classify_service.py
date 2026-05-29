"""ClassifyService — OpenAPI POST /classify의 도메인 오케스트레이션.

흐름:
  1. PreprocessPipeline로 텍스트 정규화
  2. InferencePipeline로 등급 예측 + 근거 추출
  3. **best-effort DB 영속화** (tenant·doc 존재 + DB 가용 시에만)
     실패해도 응답은 정상 — warnings에 안내만 추가 (테스트·dryrun 환경 보호)
  4. ClassifyResponse 반환 (inference_id = DB classification_id 또는 신규 UUID)
"""

from __future__ import annotations

import logging
import uuid
import warnings
from typing import Callable, Optional

from sqlalchemy.exc import SQLAlchemyError

from lloydk.modules.m2_preprocess.chunker import Chunk as _PreprocessChunk
from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline
from lloydk.modules.m5_inference.pipeline import InferencePipeline, InferenceResult
from lloydk.repositories.chunk_repo import ChunkRepo
from lloydk.repositories.classify_repo import ClassifyRepo
from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse

# A3: SSE/스트리밍에서 진척 단계를 실제로 노출하기 위한 콜백 시그니처.
# 호출 측이 None을 넘기면 기존 동작과 동일(no-op).
StageCallback = Callable[[str], None]

logger = logging.getLogger(__name__)


class ClassifyService:
    _instance: "ClassifyService | None" = None

    def __init__(self):
        self.preprocess = PreprocessPipeline()
        # Phase 3 (5070 Ti 풀가동) — 학습 가중치 디렉토리가 settings 또는
        # 환경변수에 명시되면 InferencePipeline 이 자동 로드. 미명시·미존재 시
        # rule-fallback 그대로(기존 동작 호환).
        from lloydk.config import settings as _settings  # noqa: PLC0415
        model_dir = getattr(_settings, "classifier_model_dir", "") or None
        self.inference = InferencePipeline(model_dir=model_dir)

    @classmethod
    def get_instance(cls) -> "ClassifyService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

    def classify(
        self,
        req: ClassifyRequest,
        *,
        on_stage: StageCallback | None = None,
    ) -> ClassifyResponse:
        """텍스트를 등급 분류.

        on_stage: 단계 진입 시 호출되는 콜백. 단계 이름 = stages_emitted 참조.
                  SSE 스트리밍(/classify/stream) 등에서 진척 송신용. 없으면 no-op.
        """
        notify = on_stage or (lambda _stage: None)

        logger.debug(
            "classify enter: doc_id=%s tenant=%s use_rag=%s content_len=%d",
            req.doc_id, req.tenant_id, req.use_rag, len(req.content or ""),
        )
        notify("extract")
        if req.text_already_preprocessed:
            cleaned = req.content
        else:
            notify("normalize")
            cleaned = self.preprocess.run_text(req.content)

        # 표적 2 (2026-05-29): chunks 생성. 영속화는 _try_persist 단계에서 doc/tenant 가용 시 시도.
        # PoC PreprocessPipeline.chunk()는 외부 의존 없이 항상 동작.
        try:
            chunks: list[_PreprocessChunk] = self.preprocess.chunk(cleaned) if cleaned else []
        except Exception as exc:  # noqa: BLE001
            logger.debug("classify chunk split failed (fallback []): %s", exc)
            chunks = []

        # InferencePipeline 내부에서 embed → retrieve → llm을 거치므로,
        # 진입/종료 양쪽에 신호를 보내 클라이언트가 long-stage 감지 가능.
        notify("embed")
        notify("retrieve" if req.use_rag else "llm")
        pred = self.inference.run(
            text=cleaned,
            use_rag=req.use_rag,
            rag_namespace=req.rag_namespace,
            metadata=req.metadata or {},
            return_evidence=req.return_evidence,
        )
        notify("llm")

        warnings_acc = list(pred.warnings)
        notify("persist")
        inference_id, persist_warnings = self._try_persist(req, pred, chunks=chunks)
        warnings_acc.extend(persist_warnings)
        notify("finalize")

        logger.info(
            "classify done: doc_id=%s inference_id=%s label=%s confidence=%.3f",
            req.doc_id, inference_id, pred.label, float(pred.confidence),
        )

        return ClassifyResponse(
            inference_id=inference_id,
            doc_id=req.doc_id,
            label=pred.label,
            confidence=pred.confidence,
            scores=pred.scores,
            evaluation_factors=pred.factors,
            evidence=pred.evidence,
            rag_context_used=pred.rag_context,
            model_version=pred.model_version,
            elapsed_ms=0,
            status="staging",
            warnings=warnings_acc,
        )

    # ------------------------------------------------------------
    # Persistence (best-effort)
    # ------------------------------------------------------------

    def _try_persist(
        self,
        req: ClassifyRequest,
        pred: InferenceResult,
        *,
        chunks: list[_PreprocessChunk] | None = None,
    ) -> tuple[uuid.UUID, list[str]]:
        """Best-effort 영속화.

        반환: (inference_id, warning_list)
        - 성공: classifications.classification_id 사용
        - 실패: 새 UUID + warning에 사유 기록 (예외 안 던짐)

        chunks가 주어지고 doc/tenant가 가용하면 chunks 테이블에 영속화하고
        Evidence chunk_id를 진짜 chunks row UUID로 매핑(표적 2).
        """
        warns: list[str] = []
        doc_uuid = self._parse_doc_uuid(req.doc_id)
        tenant_id = req.tenant_id or "default"

        if doc_uuid is None:
            warns.append(f"persistence skipped: doc_id={req.doc_id!r} is not a UUID")
            return uuid.uuid4(), warns

        # session_scope import는 함수 안에서 — settings.database_url 변경 가능성·테스트 격리
        try:
            from lloydk.db import session_scope  # noqa: PLC0415
        except ImportError as exc:
            warns.append(f"persistence skipped: db module unavailable ({exc})")
            return uuid.uuid4(), warns

        try:
            with session_scope() as db:
                repo = ClassifyRepo(db)
                if not repo.tenant_exists(tenant_id):
                    warns.append(f"persistence skipped: tenant_id={tenant_id!r} not found in DB")
                    return uuid.uuid4(), warns
                if not repo.document_exists(doc_uuid):
                    warns.append(f"persistence skipped: doc_id={doc_uuid} not found in documents")
                    return uuid.uuid4(), warns

                level_id = repo.level_id_by_code(pred.label)
                if level_id is None:
                    warns.append(f"persistence skipped: unknown level code {pred.label!r}")
                    return uuid.uuid4(), warns

                alternatives = [
                    {"level_code": code, "confidence": float(score)}
                    for code, score in pred.scores.items()
                    if code != pred.label.value
                ]

                # 표적 2: chunks 영속화 (있을 때만). 실패해도 classification 자체는 저장됨.
                chunk_repo = ChunkRepo(db)
                first_chunk_id: uuid.UUID | None = None
                chunk_count: int | None = None
                if chunks:
                    try:
                        ids = chunk_repo.upsert_chunks(
                            doc_id=doc_uuid,
                            tenant_id=tenant_id,
                            chunks=chunks,
                            replace_existing=True,
                        )
                        chunk_count = len(ids)
                        first_chunk_id = ids[0] if ids else None
                    except Exception as exc:  # noqa: BLE001
                        # chunks insert 실패는 분류 자체에는 영향 없음
                        logger.warning("chunk upsert failed (continuing): %s", exc)
                        warns.append(f"chunks persist failed: {type(exc).__name__}")

                cls = repo.create_classification(
                    doc_id=doc_uuid,
                    tenant_id=tenant_id,
                    model_version=pred.model_version,
                    predicted_level_id=level_id,
                    confidence=float(pred.confidence),
                    alternatives=alternatives,
                    chunk_count=chunk_count,
                    rag_used=bool(pred.rag_context),
                    rag_top_k=len(pred.rag_context) or None,
                )

                # Evidence chunk_id 결정: chunks가 영속화됐으면 진짜 첫 chunk_id, 아니면 임시 UUID
                evidence_default_chunk = first_chunk_id or uuid.uuid4()

                if pred.evidence:
                    repo.add_evidence_from_spans(
                        cls.classification_id,
                        spans=pred.evidence,
                        default_chunk_id=evidence_default_chunk,
                    )

                # 2026-05-29: RAG context도 ClassificationEvidence에 영속화.
                # rag_used=True인데 본 단계가 실패해도 classification 자체는 저장됨(트랜잭션 동일).
                if pred.rag_context:
                    n_rag = repo.add_rag_evidence_from_hits(
                        cls.classification_id,
                        hits=pred.rag_context,
                        default_chunk_id=evidence_default_chunk,
                    )
                    if n_rag != len(pred.rag_context):
                        warns.append(
                            f"rag evidence partial persist: {n_rag}/{len(pred.rag_context)}"
                        )

                return cls.classification_id, warns

        except SQLAlchemyError as exc:
            logger.error(
                "classify persistence db error: doc_id=%s err=%s",
                req.doc_id, type(exc).__name__, exc_info=True,
            )
            warns.append(f"persistence skipped: db error ({type(exc).__name__})")
            return uuid.uuid4(), warns
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "classify persistence unexpected error: doc_id=%s err=%s",
                req.doc_id, type(exc).__name__, exc_info=True,
            )
            warnings.warn(
                f"[classify_service] unexpected persistence error: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            warns.append(f"persistence skipped: unexpected error ({type(exc).__name__})")
            return uuid.uuid4(), warns

    @staticmethod
    def _parse_doc_uuid(value: str) -> Optional[uuid.UUID]:
        try:
            return uuid.UUID(value)
        except (ValueError, TypeError, AttributeError):
            return None

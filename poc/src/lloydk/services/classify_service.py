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
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline
from lloydk.modules.m5_inference.pipeline import InferencePipeline, InferenceResult
from lloydk.repositories.classify_repo import ClassifyRepo
from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse

logger = logging.getLogger(__name__)


class ClassifyService:
    _instance: "ClassifyService | None" = None

    def __init__(self):
        self.preprocess = PreprocessPipeline()
        self.inference = InferencePipeline()

    @classmethod
    def get_instance(cls) -> "ClassifyService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

    def classify(self, req: ClassifyRequest) -> ClassifyResponse:
        logger.debug(
            "classify enter: doc_id=%s tenant=%s use_rag=%s content_len=%d",
            req.doc_id, req.tenant_id, req.use_rag, len(req.content or ""),
        )
        if req.text_already_preprocessed:
            cleaned = req.content
        else:
            cleaned = self.preprocess.run_text(req.content)

        pred = self.inference.run(
            text=cleaned,
            use_rag=req.use_rag,
            rag_namespace=req.rag_namespace,
            metadata=req.metadata or {},
            return_evidence=req.return_evidence,
        )

        warnings_acc = list(pred.warnings)
        inference_id, persist_warnings = self._try_persist(req, pred)
        warnings_acc.extend(persist_warnings)

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
        self, req: ClassifyRequest, pred: InferenceResult
    ) -> tuple[uuid.UUID, list[str]]:
        """Best-effort 영속화.

        반환: (inference_id, warning_list)
        - 성공: classifications.classification_id 사용
        - 실패: 새 UUID + warning에 사유 기록 (예외 안 던짐)
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

                cls = repo.create_classification(
                    doc_id=doc_uuid,
                    tenant_id=tenant_id,
                    model_version=pred.model_version,
                    predicted_level_id=level_id,
                    confidence=float(pred.confidence),
                    alternatives=alternatives,
                    chunk_count=None,
                    rag_used=bool(pred.rag_context),
                    rag_top_k=len(pred.rag_context) or None,
                )

                if pred.evidence:
                    repo.add_evidence_from_spans(
                        cls.classification_id,
                        spans=pred.evidence,
                        default_chunk_id=uuid.uuid4(),  # PoC: 청크 영속화 전이라 임시 UUID
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

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


def _try_uuid_str(value: str | None) -> "uuid.UUID | None":
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


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
        # content가 없으면 normalized_text_uri에서 읽어오기 (doc_id 전용 분류 경로)
        content = req.content
        if not content:
            content = self._fetch_content_by_doc_id(req.doc_id, req.tenant_id or "default")
        if not content:
            warnings_acc = [f"content empty and doc_id={req.doc_id!r} not found in storage — cannot classify"]
            return ClassifyResponse(
                inference_id=uuid.uuid4(),
                doc_id=req.doc_id,
                label="S3",
                confidence=0.0,
                scores={},
                model_version="none",
                elapsed_ms=0,
                status="error",
                warnings=warnings_acc,
            )
        if req.text_already_preprocessed:
            cleaned = content
        else:
            notify("normalize")
            cleaned = self.preprocess.run_text(content)

        # 표적 2 (2026-05-29): chunks 생성. 영속화는 _try_persist 단계에서 doc/tenant 가용 시 시도.
        # PoC PreprocessPipeline.chunk()는 외부 의존 없이 항상 동작.
        try:
            chunks: list[_PreprocessChunk] = self.preprocess.chunk(cleaned) if cleaned else []
        except Exception as exc:  # noqa: BLE001
            logger.debug("classify chunk split failed (fallback []): %s", exc)
            chunks = []

        # ── Corrections 반영: DB에 검증된 라벨이 있으면 모델 추론 스킵 ───────────
        # human_review / koipa_case_based / nkt_designated 등 is_verified=True 라벨
        # 우선순위: human_review > koipa_case_based > llm_judge_consensus > ...
        verified_label = self._get_verified_label(req.doc_id)
        if verified_label is not None:
            from lloydk.schemas.common import Grade  # noqa: PLC0415
            inference_id = uuid.uuid4()
            logger.info(
                "classify doc_id=%s: verified label applied (labeled_by=%s, level=%s) — inference skipped",
                req.doc_id, verified_label.labeled_by, verified_label.level_code,
            )
            # Audit trail — best-effort DB 기록
            self._audit_verified_label(
                doc_id=req.doc_id,
                tenant_id=req.tenant_id or "default",
                level_code=verified_label.level_code,
                labeled_by=verified_label.labeled_by,
                reviewer_id=verified_label.labeler_id,
                inference_id=inference_id,
            )
            notify("finalize")
            return ClassifyResponse(
                inference_id=inference_id,
                doc_id=req.doc_id,
                label=Grade(verified_label.level_code),
                confidence=float(verified_label.confidence or 0.95),
                scores={verified_label.level_code: float(verified_label.confidence or 0.95)},
                model_version=f"human_review:{verified_label.labeled_by}",
                elapsed_ms=0,
                status="staging",
                warnings=[
                    f"verified_label_applied: labeled_by={verified_label.labeled_by},"
                    f" reviewer={verified_label.labeler_id or 'system'},"
                    f" level={verified_label.level_code} — inference skipped"
                ],
            )

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
    # Verified Label Audit Trail
    # ------------------------------------------------------------

    def _audit_verified_label(
        self,
        *,
        doc_id: str,
        tenant_id: str,
        level_code: str,
        labeled_by: str,
        reviewer_id: str | None,
        inference_id: "uuid.UUID",
    ) -> None:
        """검증된 라벨 적용 이벤트를 audit_log에 best-effort 기록."""
        try:
            from lloydk.db import SessionLocal  # noqa: PLC0415
            from lloydk.db.models import AuditLog  # noqa: PLC0415
            import datetime as _dt  # noqa: PLC0415

            db = SessionLocal()
            try:
                entry = AuditLog(
                    request_id=inference_id,
                    tenant_id=tenant_id,
                    actor_id=reviewer_id or labeled_by,
                    actor_role="human_review",
                    action="verified_label_applied",
                    target_type="document",
                    target_id=doc_id,
                    payload_hash=None,
                    success=True,
                    occurred_at=_dt.datetime.now(_dt.timezone.utc),
                )
                db.add(entry)
                db.commit()
                logger.debug("audit verified_label_applied: doc_id=%s level=%s", doc_id, level_code)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.debug("audit write failed (non-critical): %s", exc)
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("_audit_verified_label skipped: %s", exc)

    # ------------------------------------------------------------
    # Verified Label Lookup (Corrections 반영)
    # ------------------------------------------------------------

    def _get_verified_label(self, doc_id_str: str) -> "object | None":
        """doc_id에 대한 검증된 DocumentLabel 반환.

        DB 가용 시 조회. 없거나 실패하면 None (모델 추론으로 계속).
        반환 타입: DocumentLabel with .level_code, .labeled_by, .confidence 속성.
        """
        try:
            from lloydk.db import SessionLocal  # noqa: PLC0415
            from lloydk.repositories.classify_repo import ClassifyRepo  # noqa: PLC0415
            from lloydk.db.models import ClassificationLevel  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            doc_uuid = _try_uuid_str(doc_id_str)
            if doc_uuid is None:
                return None

            db = SessionLocal()
            try:
                repo = ClassifyRepo(db)
                dl = repo.get_verified_document_label(doc_uuid)
                if dl is None:
                    return None
                # level_code 조회
                level = db.execute(
                    select(ClassificationLevel).where(
                        ClassificationLevel.level_id == dl.level_id
                    )
                ).scalar_one_or_none()
                if level is None:
                    return None
                # level_code를 dl에 동적으로 attach (반환값 단순화)
                dl.level_code = level.level_code
                return dl
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("_get_verified_label failed (non-critical): %s", exc)
            return None

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
            self._inc_persist_failure("import_error")
            warns.append(f"persistence skipped: db module unavailable ({exc})")
            return uuid.uuid4(), warns

        try:
            with session_scope() as db:
                repo = ClassifyRepo(db)
                if not repo.tenant_exists(tenant_id):
                    self._inc_persist_failure("no_tenant")
                    warns.append(f"persistence skipped: tenant_id={tenant_id!r} not found in DB")
                    return uuid.uuid4(), warns
                if not repo.document_exists(doc_uuid):
                    self._inc_persist_failure("no_doc")
                    warns.append(f"persistence skipped: doc_id={doc_uuid} not found in documents")
                    return uuid.uuid4(), warns

                level_id = repo.level_id_by_code(pred.label)
                if level_id is None:
                    self._inc_persist_failure("no_level")
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
            self._inc_persist_failure("db_error")
            logger.error(
                "classify persistence db error: doc_id=%s err=%s",
                req.doc_id, type(exc).__name__, exc_info=True,
            )
            warns.append(f"persistence skipped: db error ({type(exc).__name__})")
            return uuid.uuid4(), warns
        except Exception as exc:  # noqa: BLE001
            self._inc_persist_failure("unexpected")
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

    def _fetch_content_by_doc_id(self, doc_id: str, tenant_id: str) -> str:
        """doc_id로 documents.normalized_text_uri → storage에서 텍스트 읽기.

        ingestion 완료 문서는 normalized_text_uri를 갖고 있다.
        storage·DB 미가용 시 빈 문자열 반환 — 호출자가 처리.
        """
        doc_uuid = self._parse_doc_uuid(doc_id)
        if doc_uuid is None:
            return ""
        try:
            from lloydk.adapters.storage import build_storage  # noqa: PLC0415
            from lloydk.db import session_scope  # noqa: PLC0415
            from lloydk.repositories.document_repo import DocumentRepo  # noqa: PLC0415
        except ImportError:
            return ""
        try:
            with session_scope() as db:
                doc = DocumentRepo(db).get(doc_uuid)
                if doc is None or not doc.normalized_text_uri:
                    return ""
                uri = doc.normalized_text_uri
            # file:// URI → LocalStorage, s3:// or minio:// → MinioStorage
            storage = build_storage()
            # URI → bucket/key 분해
            # 형식: s3://<bucket>/<key> 또는 minio://<host>/<bucket>/<key> 또는 file://<bucket>/<key>
            import re as _re  # noqa: PLC0415
            m = _re.match(r"(?:s3|minio|file)://([^/]+)/(.+)", uri)
            if not m:
                return ""
            bucket, key = m.group(1), m.group(2)
            data = storage.get(bucket, key)
            return data.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            logger.debug("_fetch_content_by_doc_id failed: %s", exc)
            return ""

    @staticmethod
    def _inc_persist_failure(reason: str) -> None:
        try:
            from lloydk.api.prom_metrics import CLASSIFY_PERSIST_FAILURE_TOTAL  # noqa: PLC0415
            CLASSIFY_PERSIST_FAILURE_TOTAL.labels(reason=reason).inc()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _parse_doc_uuid(value: str) -> Optional[uuid.UUID]:
        try:
            return uuid.UUID(value)
        except (ValueError, TypeError, AttributeError):
            return None

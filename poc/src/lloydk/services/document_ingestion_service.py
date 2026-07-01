"""DocumentIngestionService — 업로드 파일을 분류 대상 문서로 적재.

현장 흐름 (지금까지 코드에 없던 본체):
  파일 bytes
    → extract() 포맷 라우팅 (txt/md/csv/docx/pdf/hwp/hwpx, 미지원/스캔본은 OCR 대상 표시)
    → 원본 bytes를 object storage 저장 (raw_text_uri = 감사·증빙 원본)
    → 정규화 + PII 마스킹 + 청크 (PreprocessPipeline.run_file)
    → 정규화 텍스트도 storage 저장 (normalized_text_uri)
    → documents 행 생성 (DocumentRepo.create — provenance 1:1)
    → chunks 테이블 적재 (ChunkRepo.upsert_chunks)
  이후 ClassifyService.classify(doc_id) 가 "이미 존재하는 documents"를 소비.

설계 노트:
- 가이드 문서(GuideService)와는 별도 도메인 — 이쪽은 등급 판정 대상 비밀문서.
- DB·object storage 미가용 시 best-effort: persisted=False + warnings 로 안전 반환
  (classify_service 의 best-effort 영속화 패턴과 동일 철학).
- extract()는 path를 받으므로 임시파일 경유. 원본 진실 소스는 storage(raw_text_uri).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lloydk.adapters.storage import build_storage
from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline, PreprocessResult
from lloydk.repositories.chunk_repo import ChunkRepo
from lloydk.repositories.document_repo import DocumentRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionReviewDecision:
    """[P0#3] 저품질/OCR 추출을 분류 전 검수로 라우팅할지 판정 결과."""

    requires_review: bool
    reasons: list[str]  # extract_error | ocr | low_quality


def extraction_review_decision(
    *,
    quality: float | None,
    ocr_used: bool,
    error: str | None,
    min_quality: float,
    ocr_requires_review: bool,
) -> ExtractionReviewDecision:
    """본문이 존재하는(비어있지 않은) 추출이 검수 라우팅 대상인지 순수 판정.

    깨끗한 파서 추출과 OCR/저품질 추출이 동일 입력으로 분류기에 진입해 표 누락·깨진 문자로
    조용히 오분류되는 것을 막기 위한 게이트. FNR-safe 지향(의심스러우면 검수). 빈 본문(no_text)은
    호출부에서 processing_status='failed'로 별도 처리하므로 여기서는 다루지 않는다.

    reasons(우선순위 무관, 복수 가능): extract_error(추출 경고), ocr(OCR 사용), low_quality(품질 미만).
    """
    reasons: list[str] = []
    if error:
        reasons.append("extract_error")
    if ocr_used and ocr_requires_review:
        reasons.append("ocr")
    if quality is not None and quality < min_quality:
        reasons.append("low_quality")
    return ExtractionReviewDecision(requires_review=bool(reasons), reasons=reasons)


@dataclass
class IngestResult:
    doc_id: Optional[object]            # uuid.UUID | None (DB 미가용 시 None)
    filename: str
    source_format: str
    file_hash: str
    file_size_bytes: int
    extraction_method: str
    extraction_quality: float
    ocr_used: bool
    char_count: int
    chunk_count: int
    raw_text_uri: str
    normalized_text_uri: Optional[str]
    persisted: bool
    warnings: list[str] = field(default_factory=list)
    # [P0#3] 저품질/OCR 추출 → 분류 전 검수 라우팅 신호. requires_review=True면 문서가
    # processing_status='needs_review'로 격리돼 자동분류 그대로 통과하지 않는다.
    requires_review: bool = False
    review_reasons: list[str] = field(default_factory=list)


class DocumentIngestionService:
    """파일 업로드 → documents/chunks 적재 + 원본 보관 오케스트레이션.

    부품 주입형 — 테스트에서는 LocalStorage + stub session 으로 격리.
    운영에서는 build_storage()(MinIO) + session_scope.
    """

    RAW_BUCKET = "documents-raw"
    NORM_BUCKET = "documents-normalized"

    def __init__(self, *, storage=None, preprocess: PreprocessPipeline | None = None):
        self._storage = storage
        self.preprocess = preprocess or PreprocessPipeline()

    def _get_storage(self):
        if self._storage is None:
            self._storage = build_storage()
        return self._storage

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

    def ingest(
        self,
        *,
        filename: str,
        content_bytes: bytes,
        doc_type: Optional[str] = None,
        source_type: Optional[str] = None,
        external_ref: Optional[str] = None,
        created_by: Optional[str] = None,
        db=None,
        persist: bool = True,
    ) -> IngestResult:
        """파일 1건 적재. 실패(추출 0, DB·storage 미가용)는 warnings로 안전 반환.

        source_type: 문서 출처(provenance). 공개 출처(court_decision·공시·published_patent
            등)면 분류 시 Gate-1(비공지성 게이트)이 발동해 S3로 cap한다. documents.metadata_
            에 적재되어 doc_id 분류 경로에서 hydrate된다(ClassifyService._effective_metadata).
        """
        warns: list[str] = []
        source_format = (Path(filename).suffix.lstrip(".").lower() or "bin")[:10]
        file_hash = hashlib.sha256(content_bytes).hexdigest()
        size = len(content_bytes)
        # tenant 제거: 격리는 KL 포털 전담 — storage 키는 file_hash 단독(전역 네임스페이스).
        base_key = file_hash
        # 경로 탈출 방어 — 사용자 filename 을 그대로 스토리지 키에 합성하면
        # ..\..\ 형태로 .storage 루트 밖에 쓸 수 있다(MinIO 폴백 LocalStorage = 폐쇄망 기본).
        # 키에는 디렉터리 성분을 제거한 basename 만 사용하고, .,.. ,빈값은 해시로 대체.
        safe_name = self._safe_key_name(filename, file_hash)

        # 1) 원본 보관 (진실 소스)
        storage = self._get_storage()
        raw_uri = storage.put(self.RAW_BUCKET, f"{base_key}/{safe_name}", content_bytes)

        # 2) 추출 + 정규화 + PII 마스킹 + 청크
        pre = self._preprocess(filename, content_bytes, warns)
        ext = pre.extraction if pre else None
        text = pre.text if pre else ""

        if ext and ext.error:
            warns.append(f"extract: {ext.error}")
        if not text:
            warns.append("no text extracted (parser/OCR needed)")

        # [P0#3] 저품질/OCR 추출 → 검수 라우팅 판정 + 열화 메트릭(무음 오분류 방지).
        review_decision, degrade_reasons = self._extraction_review(ext, has_text=bool(text))
        if review_decision.requires_review:
            warns.append(
                "extraction degraded — routed to human review before classification: "
                + ", ".join(review_decision.reasons)
            )
        self._emit_extraction_degraded(degrade_reasons)

        # 3) 정규화 텍스트 보관
        norm_uri: Optional[str] = None
        if text:
            norm_uri = storage.put(
                self.NORM_BUCKET, f"{base_key}/normalized.txt", text.encode("utf-8")
            )

        # 4) DB 영속화 (best-effort)
        doc_id = None
        persisted = False
        if persist:
            doc_id, persisted, pwarns = self._persist(
                db=db,
                filename=filename,
                source_format=source_format,
                file_hash=file_hash,
                size=size,
                raw_uri=raw_uri,
                norm_uri=norm_uri,
                pre=pre,
                doc_type=doc_type,
                source_type=source_type,
                external_ref=external_ref,
                created_by=created_by,
                requires_review=review_decision.requires_review,
            )
            warns.extend(pwarns)

        # NEW-H2: 적재 카운트 — PiiMaskingMissRate 알람 분모. best-effort.
        try:
            from lloydk.api.prom_metrics import DOCUMENTS_INGESTED_TOTAL  # noqa: PLC0415

            DOCUMENTS_INGESTED_TOTAL.inc()
        except Exception:  # noqa: BLE001
            pass

        return IngestResult(
            doc_id=doc_id,
            filename=filename,
            source_format=source_format,
            file_hash=file_hash,
            file_size_bytes=size,
            extraction_method=(ext.method if ext else "none"),
            extraction_quality=(float(ext.quality) if ext else 0.0),
            ocr_used=(bool(ext.ocr_used) if ext else False),
            char_count=len(text),
            chunk_count=(len(pre.chunks) if pre else 0),
            raw_text_uri=raw_uri,
            normalized_text_uri=norm_uri,
            persisted=persisted,
            warnings=warns,
            requires_review=review_decision.requires_review,
            review_reasons=review_decision.reasons,
        )

    # ------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------

    @staticmethod
    def _safe_key_name(filename: str, fallback: str) -> str:
        """사용자 filename 에서 디렉터리 성분을 제거한 안전한 basename 반환.

        ..\\..\\evil.txt, /etc/passwd, C:\\x\\y.txt 같은 입력에서 경로 탈출 성분을
        제거한다. 결과가 비거나 '.'/'..' 이면 file_hash 로 대체(키 충돌·탈출 방지).
        백슬래시(윈도우 경로 구분자)도 분해 대상 — PurePosixPath 만으로는 누락된다.
        """
        raw = filename or ""
        # 윈도우/유닉스 구분자 모두 처리: 마지막 구분자 뒤 성분만.
        base = re.split(r"[\\/]", raw)[-1]
        base = Path(base).name.strip()
        if not base or base in {".", ".."}:
            return fallback
        return base

    def _extraction_review(self, ext, *, has_text: bool):
        """추출 결과 → (ExtractionReviewDecision, degrade_reasons[메트릭용]).

        빈 본문(no_text)은 requires_review 판정 대신 processing_status='failed'로 격리되므로
        여기서는 메트릭 reason 'empty'만 집계한다. 설정 미가용 시 합리적 기본(0.6 / OCR 검수).
        """
        try:
            from lloydk.config import settings  # noqa: PLC0415
            min_q = float(getattr(settings, "extraction_review_min_quality", 0.6))
            ocr_req = bool(getattr(settings, "extraction_ocr_requires_review", True))
        except Exception:  # noqa: BLE001
            min_q, ocr_req = 0.6, True
        if not has_text:
            return ExtractionReviewDecision(requires_review=False, reasons=[]), ["empty"]
        decision = extraction_review_decision(
            quality=(float(ext.quality) if ext else None),
            ocr_used=(bool(ext.ocr_used) if ext else False),
            error=(ext.error if ext else None),
            min_quality=min_q,
            ocr_requires_review=ocr_req,
        )
        return decision, list(decision.reasons)

    @staticmethod
    def _emit_extraction_degraded(reasons: list[str]) -> None:
        if not reasons:
            return
        try:
            from lloydk.api.prom_metrics import EXTRACTION_DEGRADED_TOTAL  # noqa: PLC0415
            for r in reasons:
                EXTRACTION_DEGRADED_TOTAL.labels(reason=r).inc()
        except Exception:  # noqa: BLE001
            pass

    def _preprocess(
        self, filename: str, content_bytes: bytes, warns: list[str]
    ) -> Optional[PreprocessResult]:
        """임시파일 경유로 extract → normalize → PII → chunk."""
        suffix = Path(filename).suffix or ".bin"
        tmp_path: Optional[str] = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, "wb") as fh:
                fh.write(content_bytes)
            return self.preprocess.run_file(tmp_path)
        except Exception as exc:  # noqa: BLE001
            warns.append(f"preprocess failed: {type(exc).__name__}: {exc}")
            return None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _persist(
        self,
        *,
        db,
        filename: str,
        source_format: str,
        file_hash: str,
        size: int,
        raw_uri: str,
        norm_uri: Optional[str],
        pre: Optional[PreprocessResult],
        doc_type: Optional[str],
        source_type: Optional[str],
        external_ref: Optional[str],
        created_by: Optional[str],
        requires_review: bool = False,
    ) -> tuple[object, bool, list[str]]:
        warns: list[str] = []
        ext = pre.extraction if pre else None
        text = pre.text if pre else ""
        # [P0#3] 저품질/OCR 추출은 'ready'로 바로 열지 않고 needs_review로 격리(자동분류 전 검수).
        status = "needs_review" if (text and requires_review) else ("ready" if text else "failed")

        def _do(session) -> object:
            doc = DocumentRepo(session).create(
                filename=filename,
                source_format=source_format,
                file_hash=file_hash,
                file_size_bytes=size,
                raw_text_uri=raw_uri,
                normalized_text_uri=norm_uri,
                text_preview=(text[:2000] or None),
                char_count=(len(text) or None),
                extraction_method=(ext.method if ext else None),
                extraction_quality=(float(ext.quality) if ext else None),
                ocr_used=(bool(ext.ocr_used) if ext else False),
                processing_status=status,
                external_ref=external_ref,
                metadata=(
                    {
                        k: v
                        for k, v in {"doc_type": doc_type, "source_type": source_type}.items()
                        if v
                    }
                    or None
                ),
                created_by=created_by,
            )
            if pre and pre.chunks:
                ChunkRepo(session).upsert_chunks(
                    doc_id=doc.doc_id,
                    chunks=pre.chunks,
                    replace_existing=True,
                )
            return doc.doc_id

        # 주입 세션 (테스트·트랜잭션 합류)
        if db is not None:
            try:
                return _do(db), True, warns
            except Exception as exc:  # noqa: BLE001
                logger.error("ingest persist failed: %s", exc, exc_info=True)
                warns.append(f"persist failed: {type(exc).__name__}")
                return None, False, warns

        # 자체 session_scope (운영 기본)
        try:
            from lloydk.db import session_scope  # noqa: PLC0415
        except ImportError as exc:
            warns.append(f"persist skipped: db module unavailable ({exc})")
            return None, False, warns
        try:
            with session_scope() as session:
                doc_id = _do(session)
            return doc_id, True, warns
        except Exception as exc:  # noqa: BLE001
            logger.error("ingest persist (scope) failed: %s", exc, exc_info=True)
            warns.append(f"persist skipped: {type(exc).__name__}")
            return None, False, warns

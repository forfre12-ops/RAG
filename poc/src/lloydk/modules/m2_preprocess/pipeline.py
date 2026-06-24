"""M2 전체 파이프라인: 파일/텍스트 → 추출 → 정규화 → 청크.

extractor.py / normalizer.py / chunker.py 가 단일 책임. 본 모듈은 조립만.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lloydk.modules.m2_preprocess.chunker import Chunk, split_v2
from lloydk.modules.m2_preprocess.extractor import ExtractResult, extract
from lloydk.modules.m2_preprocess.normalizer import normalize, quality_score
from lloydk.modules.m2_preprocess.pii_masker import mask_pii


@dataclass
class PreprocessResult:
    text: str                       # 정규화된 풀텍스트
    chunks: list[Chunk]
    extraction: ExtractResult
    quality: float                  # 0.0~1.0
    metadata: dict = field(default_factory=dict)
    pii_counts: dict = field(default_factory=dict)


class PreprocessPipeline:
    """ClassifyService 호환 인터페이스 (run_text) + 전체 파일 처리 (run_file).

    표적 6 (2026-05-29): PII 마스킹을 PreprocessPipeline에 wiring.
    settings.pii_masking_enabled가 True이면 normalize 직후 mask_pii 자동 적용.
    기본 True — 보안 민감 도메인이라 운영 안전판으로 활성.
    """

    def __init__(
        self,
        *,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        pii_masking: bool | None = None,
    ) -> None:
        try:
            from lloydk.config import settings

            self.chunk_size = chunk_size or settings.chunk_size
            self.chunk_overlap = chunk_overlap or settings.chunk_overlap
            self.pii_masking = (
                pii_masking
                if pii_masking is not None
                else bool(getattr(settings, "pii_masking_enabled", True))
            )
        except Exception:  # noqa: BLE001
            # pydantic-settings 미설치 시 합리적 기본값
            self.chunk_size = chunk_size or 512
            self.chunk_overlap = chunk_overlap or 64
            self.pii_masking = True if pii_masking is None else pii_masking

    # ClassifyService.run_text 호환 — 정규화(+PII 마스킹) 텍스트만 반환
    def run_text(self, text: str) -> str:
        normalized = normalize(text)
        if self.pii_masking:
            return mask_pii(normalized).text
        return normalized

    # 전체 파일 처리
    def run_file(self, path: str | Path) -> PreprocessResult:
        ext = extract(path)
        return self._finalize(ext)

    # 텍스트 → 풀 결과
    def run_text_full(self, text: str) -> PreprocessResult:
        ext = ExtractResult(text=text, method="plain", quality=1.0)
        return self._finalize(ext)

    def chunk(self, text: str) -> list[Chunk]:
        # #15: 운영 권장 split_v2 — 헤딩/문장 경계 보존 + heading_path 메타 적재.
        #      RAG indexer(rag/indexer.py, rag/document_indexer.py)와 동일 진입점 사용.
        #      청크 크기 정책(size/overlap)은 기존 설정값 그대로 전달해 보존한다.
        return split_v2(
            text,
            size=self.chunk_size,
            overlap=self.chunk_overlap,
            respect_heading=True,
            respect_sentence=True,
        )

    def _finalize(self, ext: ExtractResult) -> PreprocessResult:
        normalized = normalize(ext.text)
        q = quality_score(ext.text, normalized) if ext.text else 0.0
        pii_counts: dict = {}
        if self.pii_masking and normalized:
            mres = mask_pii(normalized)
            normalized = mres.text
            pii_counts = dict(mres.counts)
        # #15: split_v2로 청크 — heading_path 등 섹션 메타가 청크에 실린다.
        #      (ingestion 청크가 v1처럼 헤딩 경로를 누락하던 문제 해소.)
        chunks = split_v2(
            normalized,
            size=self.chunk_size,
            overlap=self.chunk_overlap,
            respect_heading=True,
            respect_sentence=True,
        )
        return PreprocessResult(
            text=normalized,
            chunks=chunks,
            extraction=ext,
            quality=q,
            metadata={"chunk_count": len(chunks), "pii_masked_total": sum(pii_counts.values())},
            pii_counts=pii_counts,
        )

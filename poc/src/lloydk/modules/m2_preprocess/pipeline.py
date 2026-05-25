"""M2 전체 파이프라인: 파일/텍스트 → 추출 → 정규화 → 청크.

extractor.py / normalizer.py / chunker.py 가 단일 책임. 본 모듈은 조립만.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lloydk.modules.m2_preprocess.chunker import Chunk, split
from lloydk.modules.m2_preprocess.extractor import ExtractResult, extract
from lloydk.modules.m2_preprocess.normalizer import normalize, quality_score


@dataclass
class PreprocessResult:
    text: str                       # 정규화된 풀텍스트
    chunks: list[Chunk]
    extraction: ExtractResult
    quality: float                  # 0.0~1.0
    metadata: dict = field(default_factory=dict)


class PreprocessPipeline:
    """ClassifyService 호환 인터페이스 (run_text) + 전체 파일 처리 (run_file)."""

    def __init__(self, *, chunk_size: int | None = None, chunk_overlap: int | None = None) -> None:
        try:
            from lloydk.config import settings

            self.chunk_size = chunk_size or settings.chunk_size
            self.chunk_overlap = chunk_overlap or settings.chunk_overlap
        except Exception:  # noqa: BLE001
            # pydantic-settings 미설치 시 합리적 기본값
            self.chunk_size = chunk_size or 512
            self.chunk_overlap = chunk_overlap or 64

    # ClassifyService.run_text 호환 — 정규화 텍스트만 반환
    def run_text(self, text: str) -> str:
        return normalize(text)

    # 전체 파일 처리
    def run_file(self, path: str | Path) -> PreprocessResult:
        ext = extract(path)
        return self._finalize(ext)

    # 텍스트 → 풀 결과
    def run_text_full(self, text: str) -> PreprocessResult:
        ext = ExtractResult(text=text, method="plain", quality=1.0)
        return self._finalize(ext)

    def chunk(self, text: str) -> list[Chunk]:
        return split(text, size=self.chunk_size, overlap=self.chunk_overlap)

    def _finalize(self, ext: ExtractResult) -> PreprocessResult:
        normalized = normalize(ext.text)
        q = quality_score(ext.text, normalized) if ext.text else 0.0
        chunks = split(normalized, size=self.chunk_size, overlap=self.chunk_overlap)
        return PreprocessResult(
            text=normalized,
            chunks=chunks,
            extraction=ext,
            quality=q,
            metadata={"chunk_count": len(chunks)},
        )

"""M2 전체 파이프라인: 파일/텍스트 → 추출 → 정규화 → 청크.

extractor.py / normalizer.py / chunker.py 가 단일 책임. 본 모듈은 조립만.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from koipa.modules.m2_preprocess.chunker import Chunk, split_v2
from koipa.modules.m2_preprocess.extractor import ExtractResult, extract
from koipa.modules.m2_preprocess.normalizer import normalize, quality_score
from koipa.modules.m2_preprocess.pii_masker import mask_pii

logger = logging.getLogger(__name__)

# run_text 는 문서마다 불린다 — 설정 읽기 실패를 매 건 찍으면 로그가 묻힌다. 한 번만 남긴다.
_WARNED_MASK_INPUT = False


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
            from koipa.config import settings

            self.chunk_size = chunk_size or settings.chunk_size
            self.chunk_overlap = chunk_overlap or settings.chunk_overlap
            self.pii_masking = (
                pii_masking
                if pii_masking is not None
                else bool(getattr(settings, "pii_masking_enabled", True))
            )
        except Exception as exc:  # noqa: BLE001
            # pydantic-settings 미설치 시 합리적 기본값
            self.chunk_size = chunk_size or 512
            self.chunk_overlap = chunk_overlap or 64
            self.pii_masking = True if pii_masking is None else pii_masking
            # [무음 예외] 청크 크기가 설정값과 달라지면 긴 문서가 몇 조각으로 갈리는지가
            # 바뀌고, 조각마다 나온 점수를 합산하므로 **등급이 달라질 수 있다**. 종전에는
            # 흔적이 없어 "설정 256 으로 돌았다"와 "못 읽어 512 로 돌았다"를 운영에서
            # 구분할 방법이 없었다. 미설치 폴백은 의도된 동작이므로 동작은 그대로 둔다.
            logger.warning(
                "전처리 설정을 못 읽어 기본값으로 진행 — chunk_size=%s overlap=%s "
                "pii_masking=%s (%s: %s)",
                self.chunk_size, self.chunk_overlap, self.pii_masking,
                type(exc).__name__, exc,
            )

    # ClassifyService.run_text 호환 — 분류기 *입력* 전처리(정규화). 기본 무마스킹.
    def run_text(self, text: str) -> str:
        """분류기 입력용 정규화 텍스트 반환.

        [#pii-skew] PII 마스킹은 등급 신호(내부 IP·사번·계좌/부품번호가 민감도 근거인 경우 다수)를
        지우고, 학습·평가는 무마스킹이라 서빙만 마스킹하면 OOD 브라켓 토큰으로 등급이 낮아지는
        silent FNR 스큐가 생긴다. 분류기 입력은 무마스킹이 정답(학습·평가 분포 정합) — 별도 설정
        pii_mask_classifier_input(기본 False)로만 마스킹한다. 저장/색인/표시용 마스킹은 run_file
        (_finalize) 경로가 pii_masking_enabled 로 그대로 유지 → PII 보호는 그 노출 경계에서.
        """
        normalized = normalize(text)
        try:
            from koipa.config import settings  # noqa: PLC0415
            mask_input = bool(getattr(settings, "pii_mask_classifier_input", False))
        except Exception as exc:  # noqa: BLE001
            mask_input = False
            # [무음 예외] 설정을 못 읽으면 마스킹 없이 원문이 분류기로 들어간다. 그것이
            # 의도된 기본값이지만(위 docstring 의 #pii-skew), **설정으로 켜 둔 줄 알았는데
            # 안 켜진 경우**와 구분이 안 됐다. run_text 는 문서마다 불리므로 한 번만 남긴다.
            global _WARNED_MASK_INPUT
            if not _WARNED_MASK_INPUT:
                _WARNED_MASK_INPUT = True
                logger.warning(
                    "pii_mask_classifier_input 설정을 못 읽어 마스킹 없이 진행 (%s: %s)",
                    type(exc).__name__, exc,
                )
        if mask_input:
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

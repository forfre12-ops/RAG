"""문서 단위 학습 행 → chunk 단위 확장 (FUN-004 "Chunk 단위 학습").

배경: 학습기(trainer)는 문서 1건을 1행으로 받아 max_seq_len(512 토큰)에서 truncation 한다.
긴 문서의 뒷부분(핵심 비밀이 뒤에 있을 수 있음)은 학습에서 잘려나간다. 문서를 chunk 로 쪼개
각 chunk 를 문서 라벨로 학습하면 긴 문서의 전 구간이 학습 신호가 된다.

⚠ 누수차단(감사 필수): 이 확장은 **TRAIN 분할에만** 적용해야 한다. holdout/val/test 는 문서
단위로 유지한다. build_p1_holdout_split 가 이미 문서 단위로 train↔holdout 을 분리·중복제거하므로,
그 train 을 chunk 확장해도 eval 문서는 절대 chunk 되지 않는다 → 같은 문서의 chunk 가 train/eval
양다리를 걸치는 chunk-level 누수가 원리적으로 발생하지 않는다(호출자가 train-only 계약 준수 전제).

정합: chunk 방식은 추론측(m5_inference.chunk_text → chunker.split, char size=max_seq_len*3)과
동일하게 맞춘다. 그래야 학습이 본 chunk 모양과 추론의 most-severe 집계가 도는 chunk 모양이 같다.
"""

from __future__ import annotations

import logging

from lloydk.modules.m2_preprocess.chunker import split

logger = logging.getLogger(__name__)

# 추론측 기본과 동일: 512 토큰 ≈ 1536 자(한국어) 휴리스틱(max_seq_len*3).
DEFAULT_CHAR_SIZE = 1536
DEFAULT_OVERLAP = 64
DEFAULT_MIN_CHARS = 40


def expand_chunks(
    texts: list[str],
    labels: list,
    *,
    char_size: int = DEFAULT_CHAR_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> tuple[list[str], list]:
    """문서 (text, label) 목록을 chunk 단위로 확장(라벨 상속).

    - 짧은 문서(char_size 이하 = 단일 chunk)는 그대로 1행.
    - min_chars 미만 꼬리 chunk 는 스킵하되, 전부 미달이면 원문 1행 유지(표본 유실 방지).
    - 입력 순서·라벨 대응 보존. eval 데이터 접근 없음(순수 변환 — train-only 계약은 호출자 책임).

    Returns: (out_texts, out_labels) — chunk 단위로 늘어난 학습 행.
    Raises: ValueError — texts/labels 길이 불일치.
    """
    if len(texts) != len(labels):
        raise ValueError(f"texts/labels length mismatch: {len(texts)} != {len(labels)}")

    out_x: list[str] = []
    out_y: list = []
    for text, label in zip(texts, labels):
        body = text or ""
        pieces = [c.text for c in split(body, size=char_size, overlap=overlap)]
        kept = [p for p in pieces if len(p.strip()) >= min_chars]
        if not kept:
            # 전체가 min_chars 미만이거나 빈 chunk → 원문 1행 유지(문서 표본을 잃지 않음).
            kept = [body]
        for p in kept:
            out_x.append(p)
            out_y.append(label)

    logger.info(
        "chunk expand: %d docs → %d chunk rows (char_size=%d overlap=%d min_chars=%d)",
        len(texts), len(out_x), char_size, overlap, min_chars,
    )
    return out_x, out_y

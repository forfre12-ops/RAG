"""골든셋/평가 데이터 위생(hygiene) 공유 유틸 — 정규화·텍스트해시·누출탐지·홀드아웃 분리.

여러 스크립트(build_p1_holdout_split·check_data_quality·corrections_rebuild·clean_holdout_leakage)에
제각각 흩어져 중복·불일치하던 위생 헬퍼의 정본. 통합 골든셋 빌더(및 기존 스크립트 마이그레이션)가
동일 규칙을 재사용하도록 한다. (torch 등 무거운 의존 없음)

정규화 표준(중요): 기본 = '공백 전부 제거 + 대소문자 보존'.
build_p1_holdout_split._norm과 동일하며, train↔holdout 누출 차단(commit d850126)에 쓰인 정합
기준이다. 공백/대소문자만 다른 동일 본문을 같은 키로 묶어, doc_id가 달라도 누출/중복을 잡는다.
(주의: check_data_quality는 현재 raw 텍스트를 해시해 공백차이를 못 잡음 — 이 모듈로 일원화 시 강화됨.)
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Callable, Hashable, Sequence

GRADE_LABELS = ("TS", "S1", "S2", "S3")

_WS = re.compile(r"\s+")


def normalize_text(text: object, *, keep_spaces: bool = False, lower: bool = False) -> str:
    """본문 정규화 키. 기본=공백 전부 제거, 대소문자 보존(누출/중복 차단 표준).

    keep_spaces=True → 공백을 단일 스페이스로 접고 strip (가독성 보존이 필요한 비교용).
    lower=True       → 소문자화 (영문 혼용 시 더 공격적 병합; 표준은 False).
    """
    s = str(text if text is not None else "")
    s = _WS.sub(" ", s).strip() if keep_spaces else _WS.sub("", s)
    return s.lower() if lower else s


def text_hash(text: object, *, keep_spaces: bool = False, lower: bool = False) -> str:
    """정규화 본문의 SHA1 hex. 누출/중복 탐지 키(raw 해시보다 강함 — 공백차이도 동일시)."""
    norm = normalize_text(text, keep_spaces=keep_spaces, lower=lower)
    return hashlib.sha1(norm.encode("utf-8"), usedforsecurity=False).hexdigest()


def find_leaked(
    eval_records: Sequence[dict],
    train_records: Sequence[dict],
    *,
    text_key: str = "text",
    keep_spaces: bool = False,
    lower: bool = False,
) -> list[dict]:
    """train과 본문이 겹치는(누출) eval 레코드 목록. doc_id가 달라도 본문해시로 잡는다."""
    train_hashes = {
        text_hash(r.get(text_key, ""), keep_spaces=keep_spaces, lower=lower) for r in train_records
    }
    return [
        r for r in eval_records
        if text_hash(r.get(text_key, ""), keep_spaces=keep_spaces, lower=lower) in train_hashes
    ]


def stratified_holdout_split(
    rows: Sequence[dict],
    *,
    frac: float,
    strata_key: Callable[[dict], Hashable],
    text_key: str = "text",
) -> set[str]:
    """결정론적 계층 홀드아웃 분리. 반환 = 홀드아웃에 속하는 '정규화 텍스트 키' 집합.

    동일 본문(정규화 일치)은 한 분할에만 들어가도록 텍스트키로 먼저 그룹화한 뒤,
    strata_key(예: lambda r: (r["label_source"], r["label"]))별로 정렬·상위 frac을 홀드아웃으로
    택한다. (build_p1_holdout_split.py의 분리 알고리즘과 동일 — 동일 본문이 train/eval에 양다리
    걸치는 것을 구조적으로 차단.)
    """
    by_key: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_key[normalize_text(r.get(text_key, ""))].append(r)

    strata: dict[Hashable, list[str]] = defaultdict(list)
    for key, group in by_key.items():
        strata[strata_key(group[0])].append(key)

    holdout: set[str] = set()
    for _stratum, keys in strata.items():
        keys_sorted = sorted(keys)  # 결정론적
        n_hold = int(round(len(keys_sorted) * frac))
        holdout.update(keys_sorted[:n_hold])
    return holdout

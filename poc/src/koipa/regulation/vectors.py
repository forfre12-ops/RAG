"""임베딩 ↔ 바이트. 규정 벡터는 pgvector 가 아니라 BYTEA(float32 little-endian)로 저장한다.

규정은 작다(조항 수십~수백, 문장 수백~수천) — 프로세스 메모리에서 정확 검색하는 편이 단순하고 결정형이다.
`vector(1024)` 칼럼은 SQLAlchemy 코어가 못 다뤄 alembic autogenerate 가 DROP 을 내므로(alembic/env.py
`_MIGRATION_ONLY_TABLES` 사유) 이 표들을 일반 ORM 표로 두려면 BYTEA 가 낫다.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

_DTYPE = np.dtype("<f4")


def normalize(vec: Sequence[float] | np.ndarray) -> np.ndarray:
    """L2 정규화(길이 1). 영벡터는 그대로 영벡터로 둔다(0 나눗셈 방지)."""
    v = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v if n < 1e-9 else (v / n).astype(np.float32)


def encode(vec: Sequence[float] | np.ndarray) -> bytes:
    return np.asarray(vec, dtype=_DTYPE).tobytes()


def decode(data: bytes, dim: int | None = None) -> np.ndarray:
    """바이트 → float32 벡터. 길이가 4의 배수가 아니거나 차원이 다르면 ValueError."""
    if data is None or len(data) == 0 or len(data) % _DTYPE.itemsize:
        raise ValueError("임베딩 바이트 길이가 올바르지 않습니다")
    v = np.frombuffer(data, dtype=_DTYPE).astype(np.float32)   # 복사 — 읽기 전용 버퍼를 넘기지 않는다
    if dim is not None and v.shape[0] != dim:
        raise ValueError(f"임베딩 차원이 다릅니다: {v.shape[0]} != {dim}")
    return v

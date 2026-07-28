"""[#chunk-trunc] 모델 경로 청크 절단(무음 유실) 회귀 방어.

배경: _run_model 은 char-청크(≈max_seq_len*3 자)를 max_seq_len(512) 토큰서 truncation=True 로
자르는데, 조밀한 한국어는 1536자≈680토큰이라 초과분(≈char 1127~1472 gap 밴드)이 어느 청크에도
안 실려 그 구간의 **모델-only 의미 비밀**이 미탐(FNR)됐다. _encode_windows 가 fast 토크나이저의
return_overflowing_tokens 로 초과 청크를 다중 윈도우로 무손실 커버한다. 이 테스트는
(1) 무손실 속성(절단됐을 마커 토큰이 윈도우에 복원) (2) 대조군(순수 truncation이면 유실)
(3) 짧은 문서 단일 윈도우 (4) 실모델 end-to-end 무예외를 잠근다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.slow  # 토크나이저/모델 로드

_POC = Path(__file__).resolve().parents[1]
_MODEL_DIR = _POC / "models" / "kf-deberta-labeled5k-v2" / "v-f82196b7"

_MARKER = "대외비영업비밀식별자ZZ"
# 한 문장 ≈ 20토큰. *80 ≈ 1600토큰 → 단일 청크가 512 토큰을 크게 초과(오버플로 전제).
_DENSE = "본 계약의 핵심 공정 조건과 수율 데이터는 회사의 중요한 자산이다. " * 80


def _fast_tokenizer():
    if not _MODEL_DIR.exists():
        pytest.skip("classifier model not present")
    pytest.importorskip("transformers")
    pytest.importorskip("torch")
    from transformers import AutoTokenizer  # noqa: PLC0415

    tok = AutoTokenizer.from_pretrained(str(_MODEL_DIR))
    if not getattr(tok, "is_fast", False):
        pytest.skip("tokenizer is not fast — overflow windowing unsupported (falls back to truncation)")
    return tok


def _bare_pipe_with(tok):
    from lloydk.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415

    pipe = InferencePipeline(model_dir=None)  # 모델 미로드 — 토크나이저만 주입해 _encode_windows 단위검증
    pipe._tokenizer = tok
    return pipe


def test_encode_windows_recovers_gap_band_marker():
    from lloydk.config import settings  # noqa: PLC0415

    tok = _fast_tokenizer()
    pipe = _bare_pipe_with(tok)

    long_chunk = _DENSE + " " + _MARKER  # 마커가 512 토큰 한참 뒤(절단 gap 밴드)에 위치
    marker_ids = set(tok(_MARKER, add_special_tokens=False)["input_ids"])

    # 전제 1: 마커 토큰이 body 토큰과 겹치지 않는 유니크 식별자여야 대조가 유효.
    body_ids = set(tok(_DENSE, add_special_tokens=False)["input_ids"])
    assert marker_ids - body_ids, "마커가 body 토큰과 완전 중복 — 테스트 무효"
    unique_marker_ids = marker_ids - body_ids

    # 전제 2(대조군): 순수 truncation(기존 동작)이면 마커가 512 토큰 밖이라 유실돼야 한다.
    trunc = tok([long_chunk], truncation=True, max_length=settings.max_seq_len)["input_ids"][0]
    assert not unique_marker_ids.issubset(set(trunc)), \
        "전제 실패: 마커가 max_seq_len 안에 있어 절단과 무관(테스트 무효)"

    # 본 검증: 오버플로 윈도잉은 마커를 어느 윈도우엔가 무손실 복원한다.
    enc, sample_map = pipe._encode_windows([long_chunk])
    n_windows = enc["input_ids"].shape[0]
    assert n_windows >= 2, "오버플로 청크가 다중 윈도우로 분할되지 않음"
    assert len(sample_map) == n_windows and all(s == 0 for s in sample_map)

    window_ids = {int(t) for row in enc["input_ids"] for t in row.tolist()}
    assert unique_marker_ids.issubset(window_ids), \
        "절단 gap 밴드의 마커 토큰이 어떤 윈도우에도 없음 — 무손실 실패"


def test_encode_windows_short_text_single_window():
    tok = _fast_tokenizer()
    pipe = _bare_pipe_with(tok)
    enc, sample_map = pipe._encode_windows(["짧은 정상 문서입니다. 특별한 비밀은 없다."])
    assert enc["input_ids"].shape[0] == 1
    assert sample_map == [0]


def test_run_model_end_to_end_long_doc_no_error():
    """실모델 로드 → 512토큰 초과 문서를 run()에 태워 무예외·유효 등급 반환(회귀 방어)."""
    if not _MODEL_DIR.exists():
        pytest.skip("classifier model not present")
    pytest.importorskip("transformers")
    pytest.importorskip("torch")
    from lloydk.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415
    from lloydk.schemas.common import Grade  # noqa: PLC0415

    pipe = InferencePipeline(model_dir=str(_MODEL_DIR))
    if pipe._model is None:
        pytest.skip("model failed to load (fail-closed) — rule fallback, _run_model not exercised")

    res = pipe.run(_DENSE + " " + _MARKER, return_evidence=False)
    assert isinstance(res.label, Grade)
    assert 0.0 <= res.confidence <= 1.0
    assert abs(sum(res.scores.values()) - 1.0) < 1e-3  # 확률 정규화 유지

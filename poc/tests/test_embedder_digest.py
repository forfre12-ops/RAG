"""embedder digest — 이 숫자를 어느 임베더가 만들었나.

실서빙은 require_real_embedder 가 기동을 거부하므로(api/app.py) HashEmbedding 무음 폴백이
남아 있는 곳은 **평가·학습 스크립트 경로**뿐이다. 거기서 즉시 실패로 막는 것은 과하다 —
오프라인·폐쇄망 드라이런은 정당한 작업이다. 실질적인 조치는 결과물에 무엇이 돌았는지를
박아, 나중에 그 숫자가 실 임베더 숫자와 섞이지 않게 하는 것이다.
"""
from __future__ import annotations

from koipa.adapters.embedding import (
    HashEmbedding,
    build_embedder,
    embedder_digest,
)


def test_explicit_hash_request_is_not_degraded():
    """명시적 hash 요청은 열화가 아니라 의도한 드라이런이다."""
    digest = embedder_digest(build_embedder("hash"), requested="hash")
    assert digest["effective"] == "hash"
    assert digest["degraded"] is False


def test_silent_fallback_to_hash_is_marked_degraded():
    """핵심 케이스 — 실 모델을 요청했는데 hash 가 돌아온 경우.

    build_embedder 는 HF 로드 실패 시 경고만 남기고 HashEmbedding 을 돌려준다.
    그 결과로 잰 recall 은 검색품질 근거가 아니다(HashEmbedding = 의미 없는 결정론적 해시).
    """
    digest = embedder_digest(HashEmbedding(dim=1024), requested="nlpai-lab/KURE-v1")
    assert digest["effective"] == "hash"
    assert digest["degraded"] is True
    assert digest["requested"] == "nlpai-lab/KURE-v1"


def test_digest_sees_through_cache_wrapper():
    """CachedEmbedding 으로 wrap 되면 name 이 'cached' 라 모델 구분이 사라진다.

    _underlying_name 을 우선 보지 않으면 digest 가 'cached' 만 남겨 아무것도 증명 못 한다.
    """

    class _Wrapped:
        name = "cached"
        dim = 768
        _underlying_name = "nlpai-lab/KURE-v1"

    digest = embedder_digest(_Wrapped(), requested="nlpai-lab/KURE-v1")
    assert digest["effective"] == "nlpai-lab/KURE-v1"
    assert digest["cached"] is True
    assert digest["degraded"] is False


def test_digest_is_json_serializable():
    """산출물 파일에 그대로 실을 것이므로 직렬화가 되어야 한다."""
    import json

    json.dumps(embedder_digest(HashEmbedding(dim=8), requested="hash"))


def test_eval_scripts_record_the_digest():
    """배선 고정 — digest 함수만 있고 결과물에 안 실리면 아무것도 달라지지 않는다.

    p2_eval_query_expansion 은 리포트 머리말에 'KURE-v1' 을 하드코딩하고 있었다.
    임베더가 hash 로 폴백해도 리포트에는 KURE-v1 로 적혔다는 뜻이다.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "scripts"
    compare = (root / "p2_compare_embeddings.py").read_text(encoding="utf-8")
    assert '"embedder_digest": digest' in compare

    qe = (root / "p2_eval_query_expansion.py").read_text(encoding="utf-8")
    assert "embedder_digest" in qe
    assert "KURE-v1 + ES hybrid" not in qe, "리포트 머리말에 임베더가 하드코딩돼 있다"

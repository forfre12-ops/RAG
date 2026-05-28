"""A2: AuditMiddleware → audit_chain 통합.

middleware._try_build_chained_hash가:
1) DB 가용 시 chained hash 형식(prev16:full32)으로 저장
2) DB 미가용 시 단순 sha256 폴백 (하위호환)

verify_chain 단위는 test_audit_chain.py에서 별도 검증. 본 파일은 wiring만.
"""

from __future__ import annotations

from unittest.mock import patch

from lloydk.api.middleware import _try_build_chained_hash
from lloydk.services.audit_chain import ZERO16, parse_chained_hash


def test_chain_format_when_db_available():
    """get_last_hash가 정상 반환 → 결과는 prev16:full32 형식."""
    fake_prev = "abcdef0123456789" * 4  # 64 hex
    body = b'{"x":1}'

    with patch("lloydk.services.audit_chain.get_last_hash", return_value=fake_prev[:32]):
        packed = _try_build_chained_hash(body)

    assert packed is not None
    assert ":" in packed
    prev16, full32 = parse_chained_hash(packed)
    assert prev16 == fake_prev[:16]
    assert len(full32) == 32


def test_falls_back_to_sha256_when_chain_unavailable():
    """audit_chain ImportError 시 단순 sha256(body) 반환."""
    body = b'{"x":1}'
    # get_last_hash가 예외 던지도록 패치 → 폴백 발동
    with patch("lloydk.services.audit_chain.get_last_hash", side_effect=RuntimeError("db down")):
        packed = _try_build_chained_hash(body)

    assert packed is not None
    # 단순 sha256 hex는 64자, prev16:full32는 49자
    assert ":" not in packed
    assert len(packed) == 64


def test_empty_body_returns_none_without_chain():
    """body가 비고 chain 실패 시 None (기존 동작 보존)."""
    with patch("lloydk.services.audit_chain.get_last_hash", side_effect=RuntimeError("db down")):
        packed = _try_build_chained_hash(b"")
    assert packed is None


def test_empty_body_with_chain_still_packs():
    """body가 비어도 chain은 계속 진행 (prev 연결성 검증 가치)."""
    with patch("lloydk.services.audit_chain.get_last_hash", return_value=ZERO16):
        packed = _try_build_chained_hash(b"")
    assert packed is not None
    assert ":" in packed

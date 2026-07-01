"""[무결성] 감사 체인 nil payload_hash 신호.

payload_hash가 NULL/빈값인 행은 감사 미들웨어 미경유(직접 DB 삽입) 또는 데이터 손실 신호다.
verify_chain의 break 카운트는 nil 근원 행을 무음 anchor화할 수 있어, nil 행을 따로 스캔해
nil_hash_rows + AUDIT_CHAIN_NIL_HASH_TOTAL + 경보로 노출한다(break와 별개 P0 신호).
"""

from __future__ import annotations

import contextlib
import datetime as dt
from types import SimpleNamespace

from lloydk.api import prom_metrics as pm
from lloydk.services import audit_chain as ac
from lloydk.services.audit_chain import ChainVerificationResult, verify_chain


def _rows(*hashes):
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    return [
        SimpleNamespace(payload_hash=h, audit_id=i + 1, occurred_at=base + dt.timedelta(seconds=i))
        for i, h in enumerate(hashes)
    ]


def _patch_db(monkeypatch, rows):
    @contextlib.contextmanager
    def _scope():
        class _R:
            def scalars(self_):
                return rows

        class _DB:
            def execute(self_, q):  # noqa: ARG002
                return _R()

        yield _DB()

    monkeypatch.setattr(ac, "session_scope", _scope)


def _nil_val():
    return pm.AUDIT_CHAIN_NIL_HASH_TOTAL._value.get()


# ── dataclass 판정 ────────────────────────────────────────────────────────────

def test_integrity_ok_vs_ok():
    clean = ChainVerificationResult(total_rows=3, verified=3, broken=0, nil_hash_rows=0)
    assert clean.ok() and clean.integrity_ok()
    nil = ChainVerificationResult(total_rows=3, verified=3, broken=0, nil_hash_rows=1)
    assert nil.ok()  # break 없음 → 기존 동작 보존
    assert not nil.integrity_ok()  # nil 있음 → 무결성 실패


# ── verify_chain nil 스캔 ─────────────────────────────────────────────────────

def test_nil_hash_row_detected_and_metric(monkeypatch):
    _patch_db(monkeypatch, _rows(None, "abcd0000abcd0000:ef01ef01ef01ef01ef01ef01ef01ef01"))
    before = _nil_val()
    res = verify_chain()
    assert res.nil_hash_rows == 1
    assert res.first_nil_audit_id == 1
    assert res.broken == 0  # 첫 nil 행은 break가 아님(무음 anchor) — 그래서 별도 신호가 필요
    assert not res.integrity_ok()
    assert _nil_val() == before + 1


def test_empty_or_blank_hash_counts_as_nil(monkeypatch):
    _patch_db(monkeypatch, _rows("   ", "aaaa0000aaaa0000:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"))
    res = verify_chain()
    assert res.nil_hash_rows == 1


def test_all_valid_no_nil(monkeypatch):
    _patch_db(monkeypatch, _rows(
        "0000000000000000:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "aaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ))
    before = _nil_val()
    res = verify_chain()
    assert res.nil_hash_rows == 0
    assert res.integrity_ok()
    assert _nil_val() == before  # nil 없으면 메트릭 무변동

"""Fix 4 — 원본 at-rest 봉투 암호화(EncryptingStorage) 회귀 테스트.

DB·인프라 불요 — 인메모리 fake inner storage로 암·복호화·passthrough·하위호환 검증.
"""

from __future__ import annotations

import pytest

from koipa.adapters.storage.encrypted_store import _MAGIC, EncryptingStorage

KEY = "test-encryption-secret-high-entropy-0123456789"


class _FakeInner:
    """put/get/exists/uri를 메모리 dict로 구현 — 저장된 바이트를 직접 검사."""

    name = "fake"

    def __init__(self):
        self.store: dict[tuple[str, str], bytes] = {}

    def put(self, bucket, key, data, *, content_type="application/octet-stream"):
        self.store[(bucket, key)] = data
        return f"mem://{bucket}/{key}"

    def get(self, bucket, key):
        return self.store[(bucket, key)]

    def exists(self, bucket, key):
        return (bucket, key) in self.store

    def uri(self, bucket, key):
        return f"mem://{bucket}/{key}"


def test_target_bucket_roundtrip():
    inner = _FakeInner()
    enc = EncryptingStorage(inner, key_material=KEY, buckets={"documents-raw"})
    plaintext = "영업비밀 원본 본문 ABC".encode("utf-8")
    enc.put("documents-raw", "t/x", plaintext)
    # inner에는 암호문으로 저장 (MAGIC 헤더 + 평문 미포함)
    stored = inner.store[("documents-raw", "t/x")]
    assert stored[: len(_MAGIC)] == _MAGIC
    assert plaintext not in stored
    # get은 복호화해 원본 반환
    assert enc.get("documents-raw", "t/x") == plaintext


def test_non_target_bucket_passthrough():
    inner = _FakeInner()
    enc = EncryptingStorage(inner, key_material=KEY, buckets={"documents-raw"})
    data = b"normalized masked text"
    enc.put("documents-normalized", "t/n", data)
    assert inner.store[("documents-normalized", "t/n")] == data  # 평문 그대로
    assert enc.get("documents-normalized", "t/n") == data


def test_plaintext_backward_compat():
    """대상 버킷에 기존 평문(MAGIC 없음)이 있으면 그대로 반환 — 마이그레이션 비파괴."""
    inner = _FakeInner()
    inner.store[("documents-raw", "old")] = b"legacy plaintext"
    enc = EncryptingStorage(inner, key_material=KEY, buckets={"documents-raw"})
    assert enc.get("documents-raw", "old") == b"legacy plaintext"


def test_missing_key_fail_closed():
    with pytest.raises(ValueError):
        EncryptingStorage(_FakeInner(), key_material="", buckets={"documents-raw"})


def test_strict_plaintext_rejected_for_target_bucket():
    inner = _FakeInner()
    inner.store[("documents-raw", "old")] = b"legacy plaintext"
    enc = EncryptingStorage(
        inner,
        key_material=KEY,
        buckets={"documents-raw"},
        strict_plaintext=True,
    )
    with pytest.raises(ValueError, match="plaintext object"):
        enc.get("documents-raw", "old")


def test_wrong_key_cannot_decrypt():
    inner = _FakeInner()
    EncryptingStorage(inner, key_material=KEY, buckets={"documents-raw"}).put(
        "documents-raw", "k", b"secret"
    )
    other = EncryptingStorage(inner, key_material="different-key", buckets={"documents-raw"})
    with pytest.raises(Exception):  # noqa: B017 — InvalidTag 등 복호화 실패
        other.get("documents-raw", "k")


def test_ciphertext_bound_to_bucket_and_key():
    inner = _FakeInner()
    enc = EncryptingStorage(inner, key_material=KEY, buckets={"documents-raw"})
    enc.put("documents-raw", "original", b"secret")
    inner.store[("documents-raw", "moved")] = inner.store[("documents-raw", "original")]
    with pytest.raises(Exception):  # noqa: B017 - InvalidTag from AEAD binding
        enc.get("documents-raw", "moved")


def test_build_storage_wraps_when_enabled(monkeypatch):
    from koipa import config as cfg
    from koipa.adapters import storage as st

    monkeypatch.setattr(cfg.settings, "storage_encryption_enabled", True, raising=False)
    monkeypatch.setattr(cfg.settings, "storage_encryption_key", KEY, raising=False)
    s = st.build_storage(force_local=True)
    assert isinstance(s, EncryptingStorage)


def test_build_storage_plain_when_disabled(monkeypatch):
    from koipa import config as cfg
    from koipa.adapters import storage as st

    monkeypatch.setattr(cfg.settings, "storage_encryption_enabled", False, raising=False)
    s = st.build_storage(force_local=True)
    assert not isinstance(s, EncryptingStorage)

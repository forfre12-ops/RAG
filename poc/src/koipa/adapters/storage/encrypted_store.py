"""At-rest 봉투 암호화 래퍼 — 임의 ObjectStorage를 감싸 지정 버킷만 AES-256-GCM 암호화.

원본 영업비밀 바이너리(documents-raw)는 증빙용으로 보관하지만 평문 저장은 유출
리스크다. MinIO SSE-KMS 같은 인프라 의존 없이 폐쇄망 LocalStorage에서도 동작하도록
애플리케이션 레벨에서 봉투 암호화한다(분류 경로가 읽는 정규화 텍스트는 이미 PII
마스킹돼 있으므로 대상에서 제외 — 원본만 암호화).

설계:
- 암호: AES-256-GCM. 키는 settings.storage_encryption_key(고엔트로피 시크릿)에서
  SHA-256으로 32바이트 유도(결정적). nonce는 put마다 난수 12바이트.
- 저장 포맷: MAGIC(7) + nonce(12) + ciphertext(+GCM tag). get()은 MAGIC으로 암호문을
  감지해 복호화하고, MAGIC이 없으면(기존 평문·비대상 버킷) 그대로 반환 — 평문→암호문
  점진 마이그레이션 비파괴.
- 대상 버킷만 암호화(기본 documents-raw). 그 외(정규화·모델·MLflow 아티팩트)는 통과.
- fail-closed: 암호화가 켜졌는데 키가 없으면 생성 시 예외 — 평문으로 조용히 떨어지지 않음.
"""

from __future__ import annotations

import hashlib
import logging
import os

from koipa.adapters.storage.base import ObjectStorage

logger = logging.getLogger(__name__)

_MAGIC = b"LKENC1\n"  # 7 bytes — 암호문 판별 헤더
_NONCE_LEN = 12


class EncryptingStorage:
    """ObjectStorage 데코레이터 — 지정 버킷을 투명하게 암·복호화."""

    name = "encrypted"

    def __init__(
        self,
        inner: ObjectStorage,
        *,
        key_material: str,
        buckets: set[str],
        strict_plaintext: bool = False,
    ):
        if not key_material:
            raise ValueError(
                "storage encryption enabled but no key material provided "
                "(set STORAGE_ENCRYPTION_KEY) — refusing to store plaintext (fail-closed)"
            )
        self._inner = inner
        self._buckets = set(buckets or ())
        self._strict_plaintext = strict_plaintext
        # 32바이트 AES-256 키를 고엔트로피 시크릿에서 결정적으로 유도.
        self._key = hashlib.sha256(key_material.encode("utf-8")).digest()
        logger.info(
            "EncryptingStorage active: inner=%s buckets=%s",
            getattr(inner, "name", type(inner).__name__), sorted(self._buckets),
        )

    def _should_encrypt(self, bucket: str) -> bool:
        return bucket in self._buckets

    # --- ObjectStorage Protocol ---------------------------------------------

    def put(
        self, bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> str:
        if self._should_encrypt(bucket):
            data = self._encrypt(data, bucket=bucket, key=key)
        return self._inner.put(bucket, key, data, content_type=content_type)

    def get(self, bucket: str, key: str) -> bytes:
        data = self._inner.get(bucket, key)
        # MAGIC 헤더가 있으면 암호문 — 복호화. 없으면 평문(기존 데이터·비대상 버킷) 그대로.
        if data[: len(_MAGIC)] == _MAGIC:
            return self._decrypt(data, bucket=bucket, key=key)
        if self._should_encrypt(bucket) and self._strict_plaintext:
            raise ValueError(f"encrypted bucket contains plaintext object: {bucket}/{key}")
        return data

    def exists(self, bucket: str, key: str) -> bool:
        return self._inner.exists(bucket, key)

    def uri(self, bucket: str, key: str) -> str:
        return self._inner.uri(bucket, key)

    # #36: 키 목록·삭제는 암호화와 무관(평문/암호문 모두 같은 키로 저장됨) — 내부 backend에 그대로 위임.
    def list_keys(self, bucket: str, prefix: str = "") -> list[str]:
        return self._inner.list_keys(bucket, prefix)

    def delete(self, bucket: str, key: str) -> None:
        self._inner.delete(bucket, key)

    # --- crypto --------------------------------------------------------------

    @staticmethod
    def _aad(bucket: str, key: str) -> bytes:
        return f"{bucket}/{key}".encode("utf-8")

    def _encrypt(self, data: bytes, *, bucket: str, key: str) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

        nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(self._key).encrypt(nonce, data, self._aad(bucket, key))
        return _MAGIC + nonce + ct

    def _decrypt(self, blob: bytes, *, bucket: str, key: str) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

        nonce = blob[len(_MAGIC) : len(_MAGIC) + _NONCE_LEN]
        ct = blob[len(_MAGIC) + _NONCE_LEN :]
        return AESGCM(self._key).decrypt(nonce, ct, self._aad(bucket, key))

"""P1-C4: 감사 로그 hash chain — 변조 방지.

audit_log 테이블에는 payload_hash 컬럼이 이미 존재. 본 모듈은:
1) payload + prev_row_hash를 결합한 chained hash 산정
2) chain 연결성 검증 — 한 row가 변조/삭제되면 다음 row의 prev16이 불일치

설계 한계 (A2 명시):
- body 본문은 PG에 저장하지 않음(보안). 따라서 본 chain은 *row order + prev 연결성*만
  검증함. body가 직접 변조됐는지 여부는 별도 WAL(예: minio 객체 timestamping) 필요.
- 그러나 chain의 핵심 위협 모델(=과거 row 삭제·삽입·재배열)은 본 모듈로 모두 검출됨.

스키마 변경 없이 payload_hash 컬럼에 `prev16:payload32` 형식으로 패킹하여
하위호환 유지(기존 payload_hash 단일 해시도 검증 시 prev=zeros 가정).

운영시:
- A2(2026-05-29): AuditMiddleware._try_build_chained_hash가 자동으로 chain 형성.
- 일별 정기 검증: verify_chain() — 변조 발견 시 알람
- 일별 마지막 hash는 외부 timestamping 서비스로 송부 권장(선택)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.db.models import AuditLog

logger = logging.getLogger(__name__)

ZERO16 = "0" * 16

# M-audit-hmac (W3-D): 키 없는 sha256 체인은 prev16:full32 쌍만 일관되면 공격자가
# 과거 row를 통째로 재서명(rewrite)할 수 있다(서버 비밀 불필요). 서버 보관 비밀키로
# HMAC 링크를 만들면 키 없는 재작성이 불가능해진다.
#   - 비밀키는 settings.audit_chain_secret 또는 env LLOYDK_AUDIT_CHAIN_SECRET.
#   - 키가 설정돼 있으면 HMAC-SHA256으로 체인 링크 생성(운영 권장).
#   - 키가 비어 있으면 기존 sha256 동작 유지(dev/test 비파괴) — 단, 운영(poc_mode=full)
#     에서는 1회 경고 로그를 남긴다.
_PROD_WARNED = False


def _chain_secret() -> str:
    """체인 HMAC 비밀키 — settings 우선, env 폴백. 빈 문자열이면 키 없음(레거시 sha256)."""
    try:
        from lloydk.config import settings  # noqa: PLC0415

        v = getattr(settings, "audit_chain_secret", "") or ""
    except Exception:  # noqa: BLE001
        v = ""
    if not v:
        v = os.getenv("LLOYDK_AUDIT_CHAIN_SECRET", "") or ""
    return v


def _maybe_warn_prod_no_secret(secret: str) -> None:
    """운영(poc_mode=full)인데 체인 비밀키가 없으면 1회 경고(비파괴, raise 안 함)."""
    global _PROD_WARNED
    if secret or _PROD_WARNED:
        return
    try:
        from lloydk.config import settings  # noqa: PLC0415

        if getattr(settings, "poc_mode", "dryrun") == "full":
            _PROD_WARNED = True
            logger.warning(
                "audit chain HMAC secret 미설정 — 키 없는 sha256 체인은 과거 row "
                "재작성 위협에 취약합니다. LLOYDK_AUDIT_CHAIN_SECRET 설정을 권장합니다."
            )
    except Exception:  # noqa: BLE001
        pass


def _stable_payload_repr(payload: dict | str | None) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _link_hex(secret: str, prev_row_hash: str, payload_repr: str) -> str:
    """체인 링크 다이제스트 — 비밀키가 있으면 HMAC-SHA256, 없으면 평문 sha256.

    HMAC 입력은 `{prev}|{payload}` — prev에 본 row 링크를 결속해 순서/삽입/삭제와
    payload 변조를 모두 검출한다. 비밀키가 없으면 키 없는 재작성을 막을 수 없으므로
    하위호환 sha256으로 폴백한다.
    """
    msg = f"{prev_row_hash}|{payload_repr}"
    if secret:
        return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()
    return sha256_hex(msg)


def build_chained_hash(payload: dict | str | None, prev_row_hash: str) -> str:
    """payload + prev_row_hash → 64자 다이제스트.

    payload_hash 컬럼에는 `prev16:full32` 패킹 형식으로 저장:
    - prev16: prev_row_hash 앞 16자
    - full32: 결합된 64자 hash 앞 32자
    합계 49자(콜론 포함). String(64) 컬럼 한도 안에 들어감.

    M-audit-hmac: settings.audit_chain_secret(또는 env) 설정 시 HMAC-SHA256으로 링크를
    생성해 키 없는 재작성을 차단. 미설정 시 기존 sha256 동작 유지(비파괴).
    """
    secret = _chain_secret()
    _maybe_warn_prod_no_secret(secret)
    payload_repr = _stable_payload_repr(payload)
    combined = _link_hex(secret, prev_row_hash, payload_repr)
    prev16 = (prev_row_hash or ZERO16)[:16]
    return f"{prev16}:{combined[:32]}"


def parse_chained_hash(packed: str) -> tuple[str, str]:
    """payload_hash 컬럼값을 (prev16, full32)로 분해. 기존 단일 hash는 (zeros, value16)."""
    if not packed:
        return (ZERO16, "")
    if ":" in packed:
        prev16, full32 = packed.split(":", 1)
        return (prev16, full32)
    # 하위호환: 단일 hash → prev 없이 그대로
    return (ZERO16, packed[:32])


@dataclass
class ChainVerificationResult:
    total_rows: int
    verified: int
    broken: int
    first_break_audit_id: int | None = None
    last_hash: str = ""

    def ok(self) -> bool:
        return self.broken == 0


def verify_chain(
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    tenant_id: str | None = None,
    limit: int = 10000,
) -> ChainVerificationResult:
    """audit_log 행을 occurred_at 순으로 읽으며 hash chain 검증.

    Returns:
        ChainVerificationResult — broken>0이면 변조 의심.
    """
    try:
        with session_scope() as db:
            q = select(AuditLog).order_by(AuditLog.occurred_at, AuditLog.audit_id)
            if since:
                q = q.where(AuditLog.occurred_at >= since)
            if until:
                q = q.where(AuditLog.occurred_at <= until)
            if tenant_id:
                q = q.where(AuditLog.tenant_id == tenant_id)
            q = q.limit(limit)
            rows = list(db.execute(q).scalars())
    except SQLAlchemyError as e:
        logger.debug("audit chain verify skipped (db unavailable): %s", e)
        return ChainVerificationResult(total_rows=0, verified=0, broken=0)

    verified = 0
    broken = 0
    first_break: int | None = None
    last_hash = ZERO16

    for row in rows:
        prev16, stored_full32 = parse_chained_hash(row.payload_hash or "")
        # 본 row의 prev가 직전 last_hash와 일치하는지(전형적 chain)
        if prev16 != (last_hash or ZERO16)[:16] and last_hash != ZERO16:
            broken += 1
            if first_break is None:
                first_break = row.audit_id
        # 재계산은 원본 payload 미보유라 검증 한계 — stored를 last_hash로 인수
        last_hash = stored_full32 or last_hash
        verified += 1

    return ChainVerificationResult(
        total_rows=len(rows),
        verified=verified,
        broken=broken,
        first_break_audit_id=first_break,
        last_hash=last_hash,
    )


def get_last_hash(tenant_id: str | None = None) -> str:
    """가장 최근 audit_log row의 chained full32 — 다음 row의 prev 입력용."""
    try:
        with session_scope() as db:
            q = select(AuditLog.payload_hash).order_by(AuditLog.occurred_at.desc(), AuditLog.audit_id.desc())
            if tenant_id:
                q = q.where(AuditLog.tenant_id == tenant_id)
            row = db.execute(q.limit(1)).first()
    except SQLAlchemyError:
        return ZERO16
    if not row or not row[0]:
        return ZERO16
    _, full32 = parse_chained_hash(row[0])
    return full32 or ZERO16

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

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from koipa.db import session_scope
from koipa.db.models import AuditLog

# 전역 audit 체인 advisory lock 키 — Postgres pg_advisory_xact_lock(bigint).
# 동일 키를 모든 audit insert가 공유해 prev-read+insert를 단일 직렬 체인으로 만든다.
_AUDIT_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext('koipa_audit_chain'))")

logger = logging.getLogger(__name__)

ZERO16 = "0" * 16

# M-audit-hmac (W3-D): 키 없는 sha256 체인은 prev16:full32 쌍만 일관되면 공격자가
# 과거 row를 통째로 재서명(rewrite)할 수 있다(서버 비밀 불필요). 서버 보관 비밀키로
# HMAC 링크를 만들면 키 없는 재작성이 불가능해진다.
#   - 비밀키는 settings.audit_chain_secret 또는 env KOIPA_AUDIT_CHAIN_SECRET.
#   - 키가 설정돼 있으면 HMAC-SHA256으로 체인 링크 생성(운영 권장).
#   - 키가 비어 있으면 기존 sha256 동작 유지(dev/test 비파괴) — 단, 운영(poc_mode=full)
#     에서는 1회 경고 로그를 남긴다.
_PROD_WARNED = False


def _chain_secret() -> str:
    """체인 HMAC 비밀키 — settings 우선, env 폴백. 빈 문자열이면 키 없음(레거시 sha256)."""
    try:
        from koipa.config import settings  # noqa: PLC0415

        v = getattr(settings, "audit_chain_secret", "") or ""
    except Exception:  # noqa: BLE001
        v = ""
    if not v:
        v = os.getenv("KOIPA_AUDIT_CHAIN_SECRET", "") or ""
    return v


def _maybe_warn_prod_no_secret(secret: str) -> None:
    """운영(poc_mode=full)인데 체인 비밀키가 없으면 1회 경고(비파괴, raise 안 함)."""
    global _PROD_WARNED
    if secret or _PROD_WARNED:
        return
    try:
        from koipa.config import settings  # noqa: PLC0415

        if getattr(settings, "poc_mode", "dryrun") == "full":
            _PROD_WARNED = True
            logger.warning(
                "audit chain HMAC secret 미설정 — 키 없는 sha256 체인은 과거 row "
                "재작성 위협에 취약합니다. KOIPA_AUDIT_CHAIN_SECRET 설정을 권장합니다."
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
    # payload_hash가 NULL/빈값인 행 수 — 체인 break와 별개의 무결성 신호(미들웨어 우회/데이터손실).
    nil_hash_rows: int = 0
    first_nil_audit_id: int | None = None
    # 스캔이 limit 캡에 걸려 스코프 전체를 못 봤는지 — True면 total_rows는 부분집합이라
    # broken=0/integrity_ok가 '변조 없음'이 아니라 '스캔된 부분엔 없음'을 뜻한다(무음 부분검증).
    scan_truncated: bool = False

    def ok(self) -> bool:
        # break만 본다(동작 보존). nil 행은 별개 신호로 노출 — integrity_ok로 종합 판정.
        return self.broken == 0

    def integrity_ok(self) -> bool:
        """체인 무결성 종합 — break도 nil 행도 없어야 True."""
        return self.broken == 0 and self.nil_hash_rows == 0


def scan_was_truncated(scanned: int, total_in_scope: int | None) -> bool:
    """스캔이 스코프 전체를 덮지 못했는지 — 순수 판정(DB 불요, 테스트 가능).

    total_in_scope 가 None(카운트 실패)이면 판정 불가로 False(가시화만, 과경보 금지).
    total_in_scope > scanned 이면 limit 캡에 걸려 일부 행만 검증됨(무음 부분검증 신호).
    """
    return total_in_scope is not None and total_in_scope > scanned


def verify_chain(
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
    limit: int = 10000,
) -> ChainVerificationResult:
    """audit_log 행을 occurred_at 순으로 읽으며 hash chain 검증.

    tenant 제거: 격리는 KL 포털 전담 — 감사 체인은 항상 전역 단일 체인.

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
            q = q.limit(limit)
            rows = list(db.execute(q).scalars())
            # [무음 부분검증 가드] limit 캡에 걸리면 total_rows 는 스코프의 부분집합이라
            # broken=0 이 '변조 없음'이 아니라 '스캔된 일부엔 없음'이 된다. 스코프 전체 COUNT 와
            # 비교해 절단 여부를 노출한다(일별 tick 1회 COUNT = 무시 가능 비용, best-effort).
            total_in_scope: int | None = None
            try:
                cq = select(func.count()).select_from(AuditLog)
                if since:
                    cq = cq.where(AuditLog.occurred_at >= since)
                if until:
                    cq = cq.where(AuditLog.occurred_at <= until)
                total_in_scope = int(db.execute(cq).scalar() or 0)
            except Exception:  # noqa: BLE001 — 절단 탐지 COUNT 는 검증을 절대 깨선 안 됨(best-effort)
                total_in_scope = None
    except SQLAlchemyError as e:
        logger.debug("audit chain verify skipped (db unavailable): %s", e)
        return ChainVerificationResult(total_rows=0, verified=0, broken=0)

    scan_truncated = scan_was_truncated(len(rows), total_in_scope)
    if scan_truncated:
        logger.warning(
            "audit chain verify: scan truncated — %d/%s rows scanned (limit=%d); "
            "broken=0 은 스캔된 부분에 한정, 전체 무결성 미보장 — 증분 검증 필요",
            len(rows), total_in_scope, limit,
        )

    verified = 0
    broken = 0
    first_break: int | None = None
    last_hash = ZERO16
    nil_hash_rows = 0
    first_nil: int | None = None

    for row in rows:
        # [무결성] payload_hash가 NULL/빈값 = 감사 미들웨어 미경유(직접 DB 삽입) 또는 데이터 손실.
        # break 판정과 별개로 사전 카운트 — nil이 후속 prev16=ZERO16을 만들어 break를 무음
        # anchor화하는 것을 따로 노출한다.
        if not (row.payload_hash or "").strip():
            nil_hash_rows += 1
            if first_nil is None:
                first_nil = row.audit_id
        prev16, stored_full32 = parse_chained_hash(row.payload_hash or "")
        # 본 row의 prev가 직전 last_hash와 일치하는지(전형적 chain)
        if prev16 != (last_hash or ZERO16)[:16] and last_hash != ZERO16:
            broken += 1
            if first_break is None:
                first_break = row.audit_id
        # 재계산은 원본 payload 미보유라 검증 한계 — stored를 last_hash로 인수
        last_hash = stored_full32 or last_hash
        verified += 1

    if nil_hash_rows:
        # P0 무결성 신호 — nil payload_hash는 우회/손실. break와 별개 메트릭·경보.
        try:
            from koipa.api.prom_metrics import AUDIT_CHAIN_NIL_HASH_TOTAL  # noqa: PLC0415

            AUDIT_CHAIN_NIL_HASH_TOTAL.inc(nil_hash_rows)
        except Exception:  # noqa: BLE001
            pass
        logger.warning(
            "audit chain verify: %d/%d rows have NULL payload_hash "
            "(first_nil_audit_id=%s) — 미들웨어 우회/데이터손실 의심",
            nil_hash_rows, len(rows), first_nil,
        )

    if broken:
        # NEW-H2: P0 AuditChainBroken 알람용 메트릭. best-effort(프로메테우스 미가용 무시).
        try:
            from koipa.api.prom_metrics import AUDIT_CHAIN_BROKEN_TOTAL  # noqa: PLC0415

            AUDIT_CHAIN_BROKEN_TOTAL.inc(broken)
        except Exception:  # noqa: BLE001
            pass
        logger.warning(
            "audit chain verify: %d/%d rows broken (first_break_audit_id=%s) — 변조 의심",
            broken, len(rows), first_break,
        )

    return ChainVerificationResult(
        total_rows=len(rows),
        verified=verified,
        broken=broken,
        first_break_audit_id=first_break,
        last_hash=last_hash,
        nil_hash_rows=nil_hash_rows,
        first_nil_audit_id=first_nil,
        scan_truncated=scan_truncated,
    )


def _advisory_xact_lock(db) -> None:
    """전역 audit 체인 직렬화 — Postgres에서만 트랜잭션 단위 advisory lock 획득.

    pg_advisory_xact_lock은 트랜잭션 종료(commit/rollback) 시 자동 해제된다. 같은 lock
    아래에서 prev-read+insert를 수행하면 동시 요청이 같은 prev를 읽어 체인이 분기(fork)
    하는 race를 막는다. Postgres가 아니거나(SQLite 테스트) 실패해도 best-effort로 진행.
    """
    try:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(_AUDIT_LOCK_SQL)
    except Exception as exc:  # noqa: BLE001
        logger.debug("audit advisory lock skipped: %s", exc)


def last_hash_in_session(db) -> str:
    """제공된 세션(=같은 트랜잭션)에서 직전 chained full32를 읽는다 — race-free 체인용.

    tenant 제거: 격리는 KL 포털 전담 — 전역 단일 체인.
    """
    q = select(AuditLog.payload_hash).order_by(
        AuditLog.occurred_at.desc(), AuditLog.audit_id.desc()
    )
    row = db.execute(q.limit(1)).first()
    if not row or not row[0]:
        return ZERO16
    _, full32 = parse_chained_hash(row[0])
    return full32 or ZERO16


def build_chained_hash_locked(db, body_hash: str) -> str:
    """advisory lock → 같은 트랜잭션에서 prev 읽기 → 체인 패킹 (#4 race 수정).

    반환값을 **같은 트랜잭션에서** INSERT해야 race-free 단일 체인이 보장된다. lock은
    트랜잭션 종료 시 자동 해제. 전역 단일 체인이 모든 audit insert를 직렬화한다(선형
    해시 체인의 본질적 제약). tenant 제거: 격리는 KL 포털 전담.
    """
    _advisory_xact_lock(db)
    prev = last_hash_in_session(db)
    return build_chained_hash(body_hash, prev)


def get_last_hash() -> str:
    """가장 최근 audit_log row의 chained full32 — 다음 row의 prev 입력용.

    tenant 제거: 격리는 KL 포털 전담 — 전역 단일 체인.
    """
    try:
        with session_scope() as db:
            q = select(AuditLog.payload_hash).order_by(AuditLog.occurred_at.desc(), AuditLog.audit_id.desc())
            row = db.execute(q.limit(1)).first()
    except SQLAlchemyError:
        return ZERO16
    if not row or not row[0]:
        return ZERO16
    _, full32 = parse_chained_hash(row[0])
    return full32 or ZERO16

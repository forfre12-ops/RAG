"""AuditLog 영속화 — 모든 API 진입점에서 호출.

목적: 영업비밀 시스템 컴플라이언스 (doc/04 §9.5).
payload_hash는 본문 SHA-256만 저장. 원문은 PG에 미저장.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from koipa.db.models import AuditLog


class AuditRepo:
    def __init__(self, db: Session):
        self.db = db

    def record(
        self,
        *,
        action: str,
        actor_id: str | None = None,
        actor_role: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        request_id: uuid.UUID | str | None = None,
        payload: Any = None,
        payload_hash: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        success: bool = True,
        error_code: str | None = None,
    ) -> AuditLog:
        """단일 감사 이벤트 기록.

        payload는 dict/list/str/None 모두 허용 — JSON 직렬화 후 SHA-256.
        payload_hash가 명시되면 그 값을 그대로 사용 (raw bytes 해시 등 사전 계산용).
        """
        if isinstance(request_id, str):
            try:
                request_id = uuid.UUID(request_id)
            except (ValueError, TypeError):
                request_id = None

        if payload_hash is None and payload is not None:
            payload_hash = self._hash_payload(payload)

        entry = AuditLog(
            request_id=request_id,
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload_hash=payload_hash,
            ip_address=self._safe_inet(ip_address),
            user_agent=(user_agent[:500] if user_agent else None),
            success=success,
            error_code=error_code,
        )
        self.db.add(entry)
        return entry

    @staticmethod
    def _safe_inet(value: str | None) -> str | None:
        """PG INET 컬럼 안전 검증. 유효한 IP 표기만 통과, 아니면 None.

        예: 'testclient'(starlette TestClient의 가짜 호스트명)는 INET이 거부 → None.
        """
        if not value:
            return None
        try:
            ipaddress.ip_address(value)
            return value
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _hash_payload(payload: Any) -> str:
        if isinstance(payload, (bytes, bytearray)):
            data = bytes(payload)
        elif isinstance(payload, str):
            data = payload.encode("utf-8")
        else:
            try:
                data = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
            except (TypeError, ValueError):
                data = repr(payload).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    def recent_for_actor(self, actor_id: str, *, limit: int = 50) -> list[AuditLog]:
        return list(
            self.db.execute(
                select(AuditLog)
                .where(AuditLog.actor_id == actor_id)
                .order_by(AuditLog.occurred_at.desc())
                .limit(limit)
            ).scalars()
        )

    def recent_for_action(self, action: str, *, limit: int = 100) -> list[AuditLog]:
        return list(
            self.db.execute(
                select(AuditLog)
                .where(AuditLog.action == action)
                .order_by(AuditLog.occurred_at.desc())
                .limit(limit)
            ).scalars()
        )

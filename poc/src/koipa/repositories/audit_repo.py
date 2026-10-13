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

        # 미들웨어 외의 도메인 서비스도 직접 감사 행을 남긴다(예: 비동기 규정 색인).
        # 이 경로가 빈 actor·단일 SHA-256을 그대로 넣으면 화면에는 신원 없음으로 보이고
        # 전역 체인도 끊긴다. 개인 신원이 없는 내부 작업은 사람 이름을 추정하지 않고
        # 명시적 서비스 주체로 남긴다.
        actor_id = (actor_id or "").strip() or "service:internal"
        actor_role = (actor_role or "").strip() or "system"

        if payload_hash is None and payload is not None:
            raw_payload_hash = self._hash_payload(payload)
            # HTTP 미들웨어는 이미 advisory lock 안에서 패킹된 값을 넘긴다. 여기서는
            # 도메인 서비스의 직접 기록만 같은 lock·체인 규율로 합류시킨다.
            if hasattr(self.db, "execute"):
                from koipa.services.audit_chain import build_chained_hash_locked  # noqa: PLC0415

                payload_hash = build_chained_hash_locked(self.db, raw_payload_hash)
            else:
                # DB 없는 저장소 단위 시험용 폴백. 실제 영속 경로는 위 체인 생성기를 쓴다.
                payload_hash = raw_payload_hash

        entry = AuditLog(
            request_id=request_id,
            actor_id=self._clip(actor_id, 50),
            actor_role=self._clip(actor_role, 30),
            action=self._clip(action, 50),
            target_type=self._clip(target_type, 30),
            target_id=self._clip(target_id, 100),
            payload_hash=payload_hash,
            ip_address=self._safe_inet(ip_address),
            user_agent=self._clip(user_agent, 500),
            success=success,
            error_code=self._clip(error_code, 50),
        )
        self.db.add(entry)
        return entry

    @staticmethod
    def _clip(value: str | None, max_len: int) -> str | None:
        """컬럼 길이를 넘는 값은 잘라서 담는다 — 넘기면 INSERT 가 통째로 실패해(감사 자체가
        누락된다(2026-10-03, `/classify/jobs/<uuid>` 같은 51자 경로를 `action`에 그대로 담다
        VARCHAR(50) 초과로 발견). 감사는 최선형 기록이라 길이초과로 행 전체를 잃는 것보다
        잘라서라도 남기는 쪽이 맞다 — 적어도 시도가 있었다는 사실은 남는다.
        """
        if value is None:
            return None
        return value[:max_len]

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

"""LLM 호출 비용 기록 서비스 — DB `llm_usage` 또는 JSONL 누적."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lloydk.adapters.llm.base import UsageRecord


class LLMUsageService:
    """DB 연결 가능 시 SQLAlchemy로 적재, 실패 시 로컬 JSONL."""

    def __init__(self, *, jsonl_path: str = "poc/reports/llm_usage.jsonl") -> None:
        self.jsonl_path = Path(jsonl_path)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = None
        self._try_engine()

    def _try_engine(self) -> None:
        try:
            from sqlalchemy import create_engine

            from lloydk.config import settings

            self._engine = create_engine(settings.database_url, pool_pre_ping=True)
            with self._engine.connect():
                pass
        except Exception:  # noqa: BLE001
            self._engine = None

    def record(
        self,
        usage: UsageRecord,
        *,
        purpose: str,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        tenant_id: str = "default",
        billing_phase: str = "development",
    ) -> None:
        row = {
            **asdict(usage),
            "purpose": purpose,
            "reference_type": reference_type,
            "reference_id": reference_id,
            "tenant_id": tenant_id,
            "billing_phase": billing_phase,
            "called_at": datetime.now(timezone.utc).isoformat(),
        }
        self._append_jsonl(row)
        if self._engine is not None:
            try:
                self._insert_db(row)
            except Exception:  # noqa: BLE001
                # DB 실패해도 JSONL은 남음
                pass

    def _append_jsonl(self, row: dict) -> None:
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + os.linesep)

    def _insert_db(self, row: dict) -> None:
        from sqlalchemy import text

        sql = text(
            """
            INSERT INTO llm_usage
              (provider, model, purpose, reference_type, reference_id, tenant_id,
               input_tokens, output_tokens, cost_usd, billing_phase, latency_ms,
               success, error_code, called_at)
            VALUES
              (:provider, :model, :purpose, :reference_type, :reference_id, :tenant_id,
               :input_tokens, :output_tokens, :cost_usd, :billing_phase, :latency_ms,
               :success, :error_code, :called_at)
            """
        )
        with self._engine.begin() as conn:
            conn.execute(sql, row)

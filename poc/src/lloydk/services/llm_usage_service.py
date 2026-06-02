"""LLM 호출 비용 기록 서비스 — DB `llm_usage` 또는 JSONL 누적."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lloydk.adapters.llm.base import UsageRecord

logger = logging.getLogger(__name__)


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
        except Exception as exc:  # noqa: BLE001
            logger.debug("llm_usage DB engine unavailable, falling back to JSONL: %s", exc)
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
        logger.debug(
            "llm_usage record enter: provider=%s model=%s purpose=%s tenant=%s",
            getattr(usage, "provider", None), getattr(usage, "model", None),
            purpose, tenant_id,
        )
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
            except Exception as exc:  # noqa: BLE001
                # DB 실패해도 JSONL은 남음
                logger.warning("llm_usage DB insert failed (JSONL preserved): %s", exc)
        self._emit_metrics(row, purpose)
        logger.info(
            "llm_usage recorded: provider=%s model=%s cost_usd=%s",
            row.get("provider"), row.get("model"), row.get("cost_usd"),
        )

    @staticmethod
    def _emit_metrics(row: dict, purpose: str) -> None:
        """Prometheus 토큰·비용 카운터 갱신. 메트릭 실패가 비용 기록을 막지 않도록 격리."""
        try:
            from lloydk.api.prom_metrics import (  # noqa: PLC0415
                LLM_CALLS_TOTAL,
                LLM_COST_USD_TOTAL,
                LLM_TOKENS_TOTAL,
            )
            provider = row.get("provider") or "unknown"
            model = row.get("model") or "unknown"
            in_tok = int(row.get("input_tokens") or 0)
            out_tok = int(row.get("output_tokens") or 0)
            if in_tok:
                LLM_TOKENS_TOTAL.labels(provider, model, purpose, "input").inc(in_tok)
            if out_tok:
                LLM_TOKENS_TOTAL.labels(provider, model, purpose, "output").inc(out_tok)
            cost = float(row.get("cost_usd") or 0.0)
            if cost:
                LLM_COST_USD_TOTAL.labels(provider, model, purpose).inc(cost)
            success = "true" if row.get("success", True) else "false"
            LLM_CALLS_TOTAL.labels(provider, model, purpose, success).inc()
        except Exception as exc:  # noqa: BLE001
            logger.debug("llm_usage metric emit skipped: %s", exc)

    def _append_jsonl(self, row: dict) -> None:
        # JSONL은 LF 줄 끝이 표준. CRLF는 파서 호환성 문제 야기.
        with self.jsonl_path.open("a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

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

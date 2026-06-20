"""LLM 호출 비용 영속화 — 월별 파티션 자동 라우팅."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lloydk.db.models import LlmUsage


class LlmUsageRepo:
    def __init__(self, db: Session):
        self.db = db

    def record(
        self,
        *,
        provider: str,
        model: str,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        cost_krw: float | None = None,
        tenant_id: str | None = None,
        reference_type: str | None = None,
        reference_id: str | None = None,
        billing_phase: str = "development",
        latency_ms: int | None = None,
        success: bool = True,
        error_code: str | None = None,
        called_at: dt.datetime | None = None,
    ) -> LlmUsage:
        # #28: called_at은 월별 파티션 라우팅 키. 호출부가 명시하면 그 값을,
        # 미지정(None)이면 모델의 server_default(func.now())를 따른다.
        usage = LlmUsage(
            provider=provider,
            model=model,
            purpose=purpose,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            cost_krw=cost_krw,
            tenant_id=tenant_id,
            reference_type=reference_type,
            reference_id=reference_id,
            billing_phase=billing_phase,
            latency_ms=latency_ms,
            success=success,
            error_code=error_code,
        )
        if called_at is not None:
            usage.called_at = called_at
        self.db.add(usage)
        return usage

    def total_cost_usd(
        self,
        *,
        billing_phase: str | None = None,
        tenant_id: str | None = None,
    ) -> float:
        stmt = select(func.coalesce(func.sum(LlmUsage.cost_usd), 0))
        if billing_phase:
            stmt = stmt.where(LlmUsage.billing_phase == billing_phase)
        if tenant_id:
            stmt = stmt.where(LlmUsage.tenant_id == tenant_id)
        return float(self.db.execute(stmt).scalar_one() or 0)

    def total_tokens(self, *, billing_phase: str | None = None) -> tuple[int, int]:
        """(input, output) sum."""
        stmt = select(
            func.coalesce(func.sum(LlmUsage.input_tokens), 0),
            func.coalesce(func.sum(LlmUsage.output_tokens), 0),
        )
        if billing_phase:
            stmt = stmt.where(LlmUsage.billing_phase == billing_phase)
        row = self.db.execute(stmt).one()
        return int(row[0] or 0), int(row[1] or 0)

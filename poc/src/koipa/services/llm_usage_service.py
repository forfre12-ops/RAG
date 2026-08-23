"""LLM 호출 비용 기록 서비스 — DB `tb_llm_usage` 또는 JSONL 누적."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from koipa.adapters.llm.base import UsageRecord

logger = logging.getLogger(__name__)


class LLMUsageService:
    """DB 연결 가능 시 공유 풀(session_scope)로 적재, 실패 시 로컬 JSONL.

    #28: 과거에는 __init__에서 자체 create_engine + connect 테스트를 하고
    _insert_db도 별도 풀(self._engine.begin())을 가져, db/session.py의 공유
    engine/풀(pool_size·pool_pre_ping)을 우회했다(커넥션 누수·풀 분열). 또
    repositories/llm_usage_repo.py가 데드코드였다. 이제 공유 ``session_scope()``
    와 ``LlmUsageRepo``를 통해 적재한다 — 자체 엔진/풀을 제거하고 풀을 단일화.
    DB 미가용 시 JSONL 폴백(best-effort) 동작은 그대로 보존한다.
    """

    def __init__(self, *, jsonl_path: str = "poc/reports/llm_usage.jsonl") -> None:
        self.jsonl_path = Path(jsonl_path)
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        usage: UsageRecord,
        *,
        purpose: str,
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
        billing_phase: str = "development",
    ) -> None:
        # tenant 제거: 격리는 KL 포털 전담 — LLM 비용은 전역 집계(tenant 태그 없음).
        logger.debug(
            "llm_usage record enter: provider=%s model=%s purpose=%s",
            getattr(usage, "provider", None), getattr(usage, "model", None),
            purpose,
        )
        called_at = datetime.now(timezone.utc)
        # 배포 이미지가 실제 실행한 커밋을 비용 증적에도 남긴다. 값이 없거나 unknown이면
        # 집계기가 운영 revision 비용으로 승인하지 않도록 null로 기록한다.
        build_sha = (os.getenv("KOIPA_BUILD_SHA") or "").strip()
        row = {
            **asdict(usage),
            "build_sha": build_sha if build_sha and build_sha.lower() != "unknown" else None,
            "purpose": purpose,
            "reference_type": reference_type,
            "reference_id": reference_id,
            "billing_phase": billing_phase,
            "called_at": called_at.isoformat(),
        }
        # JSONL은 항상 먼저 남긴다 — DB 적재가 실패해도 비용 기록은 보존.
        self._append_jsonl(row)
        # #28: 공유 풀(session_scope) + LlmUsageRepo로 best-effort 적재.
        # Isolated corpus-generation runners can have no PostgreSQL service.
        # Keep their append-only JSONL audit while avoiding one connection
        # timeout per LLM call.  Production keeps the existing DB behaviour
        # unless the runner explicitly opts out.
        if os.getenv("KOIPA_LLM_USAGE_DB_ENABLED", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }:
            self._insert_db(usage, purpose, reference_type, reference_id,
                            billing_phase, called_at)
        self._emit_metrics(row, purpose)
        logger.info(
            "llm_usage recorded: provider=%s model=%s cost_usd=%s",
            row.get("provider"), row.get("model"), row.get("cost_usd"),
        )

    @staticmethod
    def _emit_metrics(row: dict, purpose: str) -> None:
        """Prometheus 토큰·비용 카운터 갱신. 메트릭 실패가 비용 기록을 막지 않도록 격리."""
        try:
            from koipa.api.prom_metrics import (  # noqa: PLC0415
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

    def _insert_db(
        self,
        usage: UsageRecord,
        purpose: str,
        reference_type: Optional[str],
        reference_id: Optional[str],
        billing_phase: str,
        called_at: datetime,
    ) -> None:
        """#28: 공유 session_scope() + LlmUsageRepo 경유 적재(best-effort).

        DB 미가용·적재 실패 시 예외를 흡수한다 — JSONL은 이미 남았으므로
        비용 기록 자체는 보존된다(기존 폴백 의미 유지). session_scope import는
        함수 안에서 — settings.database_url 변경 가능성·테스트 격리 보장.
        """
        try:
            from koipa.db import session_scope  # noqa: PLC0415
            from koipa.repositories.llm_usage_repo import LlmUsageRepo  # noqa: PLC0415

            with session_scope() as db:
                LlmUsageRepo(db).record(
                    provider=usage.provider,
                    model=usage.model,
                    purpose=purpose,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=usage.cost_usd,
                    reference_type=reference_type,
                    reference_id=reference_id,
                    billing_phase=billing_phase,
                    latency_ms=getattr(usage, "latency_ms", None) or None,
                    success=usage.success,
                    error_code=usage.error_code,
                    called_at=called_at,
                )
        except Exception as exc:  # noqa: BLE001
            # DB 실패해도 JSONL은 남음 (best-effort).
            logger.warning("llm_usage DB insert failed (JSONL preserved): %s", exc)


# ── [QW] 호출지점용 best-effort 비용 기록 헬퍼 ────────────────────────────────
# LLMUsageService.record가 src 어디서도 호출되지 않아 synthesis/query_expansion/answer 등
# 모든 LLM 비용이 미기록(llm_cost_usd_total 0)이었다. 각 호출지점에서 이 헬퍼를 부르면
# LLMResponse(.usage) 또는 UsageRecord를 받아 best-effort로 기록한다 — 실패는 흡수해 LLM
# 호출 경로(특히 폴백 로직)를 절대 막지 않는다. purpose 라벨로 경로별 비용을 분리 집계.
_default_service: "LLMUsageService | None" = None


def _get_default_service() -> "LLMUsageService":
    global _default_service
    if _default_service is None:
        _default_service = LLMUsageService()
    return _default_service


def record_llm_usage(
    resp_or_usage: object,
    *,
    purpose: str,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    billing_phase: Optional[str] = None,
    service: "LLMUsageService | None" = None,
) -> None:
    """LLMResponse(.usage) 또는 UsageRecord를 받아 best-effort로 LLM 사용/비용 기록.

    실패는 전부 흡수(LLM 호출 경로 무영향). billing_phase 미지정 시 settings.llm_billing_phase.
    """
    try:
        usage = getattr(resp_or_usage, "usage", None)
        if usage is None:
            usage = resp_or_usage  # 이미 UsageRecord일 수 있음
        if usage is None or not hasattr(usage, "provider"):
            return  # 텍스트만 반환하는 provider(usage 없음) — 기록 불가, 무해 skip
        if billing_phase is None:
            try:
                from koipa.config import settings  # noqa: PLC0415
                billing_phase = getattr(settings, "llm_billing_phase", "development")
            except Exception:  # noqa: BLE001
                billing_phase = "development"
        svc = service or _get_default_service()
        svc.record(
            usage,
            purpose=purpose,
            reference_type=reference_type,
            reference_id=reference_id,
            billing_phase=billing_phase,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_llm_usage skipped: %s", exc)

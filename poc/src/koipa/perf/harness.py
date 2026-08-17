"""ScenarioRunner — 시나리오 단위 측정 실행기.

설계:
- 각 시나리오는 dict[str, list[value]] 형태로 측정값 누적
- KPI는 (scenario, metric_key)로 측정값 골라 aggregator로 환산
- 외부 의존(pg/es/redis/llm) 미충족 시 SKIP, 합격선 판정에서 제외
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

from koipa.perf.kpis import KPI, KPIS, aggregate, passes


@dataclass
class Measurement:
    scenario: str
    key: str
    value: float | bool
    unit: str = ""


@dataclass
class KPIResult:
    kpi_id: str
    scenario: str
    name: str
    unit: str
    measured: float
    threshold: float | bool
    compare: str
    passed: bool
    status: str  # "PASS" | "FAIL" | "SKIP"
    skip_reason: str = ""
    n_samples: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScenarioResult:
    scenario: str
    title: str
    status: str  # "PASS" | "FAIL" | "SKIP" | "ERROR"
    duration_ms: float
    measurements: dict[str, list[float | bool]] = field(default_factory=dict)
    kpis: list[KPIResult] = field(default_factory=list)
    error: str = ""
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kpis"] = [k.to_dict() if hasattr(k, "to_dict") else k for k in self.kpis]
        return d


@dataclass
class HarnessReport:
    mode: str  # "dryrun" | "full"
    ts: str
    duration_sec: float
    env: dict[str, Any]
    scenarios: list[ScenarioResult]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ts": self.ts,
            "duration_sec": self.duration_sec,
            "env": self.env,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "summary": self.summary,
        }


@dataclass
class AvailableResources:
    """외부 자원 가용성 — KPI requires 와 매칭."""

    pg: bool = False
    es: bool = False
    redis: bool = False
    minio: bool = False
    llm: bool = False  # noop은 false (실제 LLM 시 true)
    gpu: bool = False
    # 학습된 분류 모델 (KF-DeBERTa·KoELECTRA 등). dryrun에서는 룰 fallback이라 false.
    # KPI 중 FNR·F1·Recall@5처럼 실 모델 추론 결과에 의미가 부여되는 지표만 require에 추가.
    trained_model: bool = False

    @classmethod
    def from_env_snapshot(cls, env: Any, llm_provider: str) -> "AvailableResources":
        svc = env.services
        return cls(
            pg=svc.postgres == "UP",
            es=svc.elasticsearch == "UP",
            redis=svc.redis == "UP",
            minio=svc.minio == "UP",
            llm=llm_provider not in ("noop", "", None),
            gpu=env.gpu != "N/A",
            trained_model=_detect_trained_model(),
        )

    def has(self, name: str) -> bool:
        return bool(getattr(self, name, False))


def _detect_trained_model() -> bool:
    """active model이 DB에 등록돼 있고 model_uri가 비어있지 않으면 학습된 모델로 간주.

    실패(DB 미가용·미설치 등) 시 False — dryrun 환경 안전.
    """
    try:
        import os

        # 명시적 환경변수가 우선 (CI에서 강제 켜기 가능)
        env_flag = os.environ.get("KOIPA_TRAINED_MODEL", "").lower()
        if env_flag in ("1", "true", "yes"):
            return True
        if env_flag in ("0", "false", "no"):
            return False

        from sqlalchemy import select

        from koipa.db import session_scope
        from koipa.db.models import ModelVersion

        with session_scope() as db:
            mv = db.execute(
                select(ModelVersion).where(ModelVersion.is_active.is_(True))
            ).scalar_one_or_none()
            if mv is None:
                return False
            # model_uri 있고, base_model이 룰 폴백이 아니라면 학습된 것
            uri = (mv.model_uri or "").strip()
            return bool(uri)
    except Exception:
        return False


@dataclass
class ScenarioSpec:
    """시나리오 1개의 실행 명세."""

    id: str  # S1
    title: str
    runner: Callable[["ScenarioContext"], None]
    requires: list[str] = field(default_factory=list)  # ["pg"] 등 (스킵 우선판단)


class ScenarioContext:
    """시나리오 함수가 측정값을 적재하는 컨텍스트.

    사용:
        ctx.record("latency_ms", 320.5)
        ctx.record("schema_valid", True)
    """

    def __init__(self, scenario_id: str, resources: AvailableResources, mode: str) -> None:
        self.scenario_id = scenario_id
        self.resources = resources
        self.mode = mode
        self.measurements: dict[str, list[float | bool]] = {}
        self.skip_reason: str = ""
        self.skipped: bool = False

    def record(self, key: str, value: float | bool) -> None:
        self.measurements.setdefault(key, []).append(value)

    def skip(self, reason: str) -> None:
        self.skipped = True
        self.skip_reason = reason


class ScenarioRunner:
    """시나리오 N개 실행 + KPI 환산 + Skip 처리."""

    def __init__(
        self,
        *,
        mode: str,
        resources: AvailableResources,
        kpis: list[KPI] | None = None,
    ) -> None:
        self.mode = mode
        self.resources = resources
        self.kpis = kpis or KPIS

    def run(self, specs: list[ScenarioSpec]) -> list[ScenarioResult]:
        import os
        import time

        # 시나리오 사이 간격(초). 서빙 레이트리밋은 분 단위 창이라(분류 60/min) 전 시나리오를
        # 붙여 돌리면 뒤쪽이 429 를 맞고 그 값이 "미달" 로 집계된다 — 223 실측 2026-08-17:
        # 27초 안에 분류 ~56건이 몰려 S16.4 가 0.0 이었고, 단독 실행하면 1.0 이었다.
        # 운영 프로파일에서는 레이트리밋을 끌 수 없으므로(config fail-clear) 간격으로 푼다.
        pace = float(os.environ.get("PSH_PACE_SEC", "0") or 0)

        results: list[ScenarioResult] = []
        for idx, spec in enumerate(specs):
            if pace > 0 and idx > 0:
                time.sleep(pace)
            t0 = time.perf_counter()
            ctx = ScenarioContext(spec.id, self.resources, self.mode)

            missing = [r for r in spec.requires if not self.resources.has(r)]
            try:
                if missing and self.mode == "full":
                    ctx.skip(f"missing resources: {','.join(missing)}")
                else:
                    spec.runner(ctx)
                error = ""
            except Exception as e:
                error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

            duration_ms = (time.perf_counter() - t0) * 1000.0
            kpi_results = self._compute_kpis(spec.id, ctx)

            if error:
                status = "ERROR"
            elif ctx.skipped:
                status = "SKIP"
            elif any(k.status == "FAIL" for k in kpi_results):
                status = "FAIL"
            elif kpi_results and all(k.status in ("PASS", "SKIP") for k in kpi_results):
                status = "PASS"
            else:
                status = "PASS" if not kpi_results else "PASS"

            results.append(
                ScenarioResult(
                    scenario=spec.id,
                    title=spec.title,
                    status=status,
                    duration_ms=duration_ms,
                    measurements=ctx.measurements,
                    kpis=kpi_results,
                    error=error,
                    skip_reason=ctx.skip_reason,
                )
            )
        return results

    def _compute_kpis(self, scenario_id: str, ctx: ScenarioContext) -> list[KPIResult]:
        out: list[KPIResult] = []
        for kpi in self.kpis:
            if kpi.scenario != scenario_id:
                continue
            missing = [r for r in kpi.requires if not self.resources.has(r)]
            # requires 는 mode 와 무관하게 건다. `and self.mode == "full"` 이던 이전 판은
            # 정반대로 동작했다 — dryrun 에서 trained_model 이 없는데도 S1.3(F1)·S1.4(FNR)·
            # S9.2 를 측정해 룰 폴백의 시드 키워드 정답을 "F1 100%" 로 보고서에 실었다.
            # 자원이 없으면 그 KPI 는 측정한 것이 아니라 SKIP 이다.
            mode_gated = getattr(kpi, "full_only", False) and self.mode != "full"
            if ctx.skipped or missing or mode_gated:
                out.append(
                    KPIResult(
                        kpi_id=kpi.id,
                        scenario=kpi.scenario,
                        name=kpi.name,
                        unit=kpi.unit,
                        measured=0.0,
                        threshold=kpi.threshold,
                        compare=kpi.compare,
                        passed=False,
                        status="SKIP",
                        skip_reason=(
                            ctx.skip_reason
                            or (f"missing: {','.join(missing)}" if missing else "")
                            or "full 모드 실측만 판정 대상 (dryrun 값은 판정 근거 아님)"
                        ),
                        n_samples=0,
                    )
                )
                continue

            key = _measurement_key_for_kpi(kpi)
            values = ctx.measurements.get(key, [])
            if not values:
                out.append(
                    KPIResult(
                        kpi_id=kpi.id,
                        scenario=kpi.scenario,
                        name=kpi.name,
                        unit=kpi.unit,
                        measured=0.0,
                        threshold=kpi.threshold,
                        compare=kpi.compare,
                        passed=False,
                        status="SKIP",
                        skip_reason=f"no measurement for key '{key}'",
                        n_samples=0,
                    )
                )
                continue

            measured = aggregate(values, kpi.aggregator)
            ok = passes(measured, kpi.compare, kpi.threshold)
            out.append(
                KPIResult(
                    kpi_id=kpi.id,
                    scenario=kpi.scenario,
                    name=kpi.name,
                    unit=kpi.unit,
                    measured=measured,
                    threshold=kpi.threshold,
                    compare=kpi.compare,
                    passed=ok,
                    status="PASS" if ok else "FAIL",
                    n_samples=len(values),
                )
            )
        return out


def _measurement_key_for_kpi(kpi: KPI) -> str:
    """KPI ID와 매칭되는 측정 key.

    규약: 시나리오 함수는 KPI.id를 lowercase로 변환해 key로 사용.
    예: KPI "S1.1" → key "s1_1"
    """
    return kpi.id.lower().replace(".", "_")


def summarize(results: list[ScenarioResult]) -> dict[str, Any]:
    all_kpis: list[KPIResult] = [k for s in results for k in s.kpis]
    total = len(all_kpis)
    n_pass = sum(1 for k in all_kpis if k.status == "PASS")
    n_fail = sum(1 for k in all_kpis if k.status == "FAIL")
    n_skip = sum(1 for k in all_kpis if k.status == "SKIP")
    return {
        "total_kpis": total,
        "pass": n_pass,
        "fail": n_fail,
        "skip": n_skip,
        "pass_rate": (n_pass / total) if total else 0.0,
        "scenarios": {
            "total": len(results),
            "pass": sum(1 for s in results if s.status == "PASS"),
            "fail": sum(1 for s in results if s.status == "FAIL"),
            "skip": sum(1 for s in results if s.status == "SKIP"),
            "error": sum(1 for s in results if s.status == "ERROR"),
        },
    }

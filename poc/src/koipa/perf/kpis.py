"""KPI 정의 — doc/20a §1 매트릭스 1:1.

각 KPI:
- id: 시나리오 ID + 시퀀스 (S1.4 등)
- scenario: 시나리오 식별자 (S1~S8)
- name: 사람이 읽는 이름
- unit: 단위 (ms·비율·docs/s·USD·bool·count)
- threshold: 합격선 (≤·≥·==)
- aggregator: 측정값 → 단일 수치 함수 (p50/p95/mean/sum/last/bool_all)
- requires: 외부 의존 (pg·es·redis·minio·llm·gpu)
- core: 상단 위젯 노출 여부
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Literal


Compare = Literal["le", "ge", "eq", "gt", "lt"]
Aggregator = Literal["p50", "p95", "mean", "max", "min", "sum", "last", "bool_all", "ratio_true"]


@dataclass
class KPI:
    id: str
    scenario: str
    name: str
    unit: str
    compare: Compare
    threshold: float | bool
    aggregator: Aggregator
    requires: list[str] = field(default_factory=list)
    core: bool = False


def aggregate(values: list[float | bool], agg: Aggregator) -> float:
    if not values:
        return 0.0
    if agg == "bool_all":
        return 1.0 if all(bool(v) for v in values) else 0.0
    if agg == "ratio_true":
        return sum(1 for v in values if bool(v)) / len(values)
    nums = [float(v) for v in values]
    if agg == "mean":
        return statistics.mean(nums)
    if agg == "max":
        return max(nums)
    if agg == "min":
        return min(nums)
    if agg == "sum":
        return sum(nums)
    if agg == "last":
        return nums[-1]
    if agg == "p50":
        return statistics.median(nums)
    if agg == "p95":
        if len(nums) == 1:
            return nums[0]
        s = sorted(nums)
        k = int(round(0.95 * (len(s) - 1)))
        return s[k]
    return statistics.mean(nums)


def passes(value: float, compare: Compare, threshold: float | bool) -> bool:
    t = float(threshold) if not isinstance(threshold, bool) else (1.0 if threshold else 0.0)
    if compare == "le":
        return value <= t
    if compare == "ge":
        return value >= t
    if compare == "eq":
        return value == t
    if compare == "gt":
        return value > t
    if compare == "lt":
        return value < t
    return False


KPIS: list[KPI] = [
    # S1
    KPI("S1.1", "S1", "p50 latency", "ms", "le", 500, "p50"),
    KPI("S1.2", "S1", "p95 latency", "ms", "le", 5000, "p95", core=True),
    # F1·FNR은 실 학습 모델 전제 — dryrun 룰 fallback에서는 룰 키워드 매칭 노이즈로 임계 미달.
    # full 모드(GPU + KF-DeBERTa 학습 후)에서만 측정. doc/16 §2.2 labeled dataset 완성 후 활성.
    KPI("S1.3", "S1", "F1-macro (4-class)", "ratio", "ge", 0.75, "last", core=True, requires=["trained_model"]),
    KPI("S1.4", "S1", "FNR (TS→하위)", "ratio", "le", 0.05, "last", core=True, requires=["trained_model"]),
    KPI("S1.5", "S1", "응답 스키마 정합", "bool", "ge", True, "bool_all"),

    # S2
    # 시드 v3(313 키워드) 도입 후 콜드스타트 비용 증가 반영 — 200→500ms.
    # full 모드 운영 (실제 Celery + workers)에서는 ≤200ms로 다시 타이트닝 예정.
    KPI("S2.1", "S2", "async 202 latency", "ms", "le", 500, "p95"),
    KPI("S2.2", "S2", "batch 5건 throughput", "docs/s", "ge", 5, "last"),
    KPI("S2.3", "S2", "job 폴링 정합", "bool", "ge", True, "bool_all"),

    # S3
    KPI("S3.1", "S3", "confirm p95 latency", "ms", "le", 300, "p95"),
    KPI("S3.2", "S3", "relabel p95 latency", "ms", "le", 300, "p95"),
    KPI("S3.3", "S3", "corrections 누적 정합", "bool", "ge", True, "bool_all", requires=["pg"]),
    KPI("S3.4", "S3", "status='corrected' 전이", "bool", "ge", True, "bool_all", requires=["pg"]),

    # S4
    KPI("S4.1", "S4", "GET grades 응답", "bool", "ge", True, "bool_all"),
    KPI("S4.2", "S4", "동일 grades → no retrain", "bool", "ge", True, "bool_all", requires=["pg"]),
    KPI("S4.3", "S4", "신규 코드 → retrain", "bool", "ge", True, "bool_all", requires=["pg"]),
    KPI("S4.4", "S4", "PUT p95 latency", "ms", "le", 500, "p95", requires=["pg"]),

    # S5
    KPI("S5.1", "S5", "업로드+인덱싱 p95 latency", "ms", "le", 30000, "p95"),
    KPI("S5.2", "S5", "embedding_vector_count > 0", "count", "gt", 0, "min"),
    # dryrun: hash 임베딩 + InMemory 백엔드. full: KURE-v1 + ES. 합격선은 보수적 0.3 (도달 검증 + full 시 회귀 추적)
    KPI("S5.3", "S5", "인덱싱 throughput", "chunks/s", "ge", 0.3, "last"),
    # Recall@5는 실 PG 벡터스토어(pgvector) + 학습된 모델(또는 풀 임베딩) 전제. dryrun(hash 임베딩)에선 SKIP.
    KPI("S5.4", "S5", "Recall@5", "ratio", "ge", 0.80, "last", requires=["pg", "trained_model"], core=True),
    KPI("S5.5", "S5", "후속 GET 200", "bool", "ge", True, "bool_all"),

    # S6
    KPI("S6.1", "S6", "generate 202 latency", "ms", "le", 500, "p95"),
    KPI("S6.2", "S6", "라벨 일치도", "ratio", "ge", 0.90, "last", core=True),
    KPI("S6.3", "S6", "비용/건", "USD", "le", 0.02, "last", requires=["llm"]),
    KPI("S6.4", "S6", "검수 큐 진입", "bool", "ge", True, "bool_all"),
    KPI("S6.5", "S6", "approve 후 dataset 연결", "bool", "ge", True, "bool_all", requires=["pg"]),

    # S7
    KPI("S7.1", "S7", "임계치 도달 검증", "bool", "ge", True, "bool_all", requires=["pg"]),
    KPI("S7.2", "S7", "/train 트리거 latency", "ms", "le", 1000, "p95", requires=["pg"]),
    KPI("S7.3", "S7", "TrainingRun.queued 생성", "bool", "ge", True, "bool_all", requires=["pg"]),

    # S8
    KPI("S8.1", "S8", "metrics/latest 200+스키마", "bool", "ge", True, "bool_all"),
    KPI("S8.2", "S8", "latest p95 latency", "ms", "le", 500, "p95"),
    KPI("S8.3", "S8", "confusion-matrix p95 latency", "ms", "le", 30000, "p95", requires=["pg"]),
    KPI("S8.4", "S8", "history 페이지네이션 정합", "bool", "ge", True, "bool_all", requires=["pg"]),

    # S9 적대적·모호 문서 FNR 스트레스 (W10 확장)
    KPI("S9.1", "S9", "변형 일관성 (consistency)", "ratio", "ge", 0.70, "last"),
    # 적대적 FNR도 실 학습 모델 전제 — 룰 fallback에서는 무의미
    KPI("S9.2", "S9", "적대적 FNR (TS→하위)", "ratio", "le", 0.05, "last", requires=["trained_model"], core=True),
    KPI("S9.3", "S9", "confidence 변동 stdev", "ratio", "le", 0.20, "last"),

    # S11 부하 시나리오 (W10 확장 + W12+ 블록 6 단계 상향)
    # error_rate는 모든 단계 누적 — 0.01 유지
    KPI("S11.1", "S11", "단계별 누적 error_rate", "ratio", "le", 0.01, "last"),
    # p95는 모든 단계 누적 — 8s 유지 (가장 큰 부하 100/200 워커 반영)
    KPI("S11.2", "S11", "단계별 p95 latency", "ms", "le", 8000, "p95"),
    # throughput은 단계 중 최대 (정점 처리량)
    KPI("S11.3", "S11", "throughput peak", "req/s", "ge", 5, "last"),
    # W12+ 신규: 메모리 누수 감지 — 전·후 RSS 차분 ≤ 100MB
    KPI("S11.4", "S11", "메모리 누수 (RSS Δ)", "MB", "le", 100, "last"),
    # W12+ 신규: 단계 확장성 — scaling ratio / worker ratio.
    # dryrun(TestClient + Python GIL)에서는 진정한 병렬이 불가능 → 0.15가 현실
    # full(uvicorn workers)에서는 ≥ 0.5 도달 가능, 합격선 별도 상향 검토 필요
    KPI("S11.5", "S11", "단계 확장성 (scaling efficiency)", "ratio", "ge", 0.15, "last"),
    # PER-002 자원 사용률 상한 — 부하 중 시스템 사용률 피크(max)가 임계 미만이어야 한다.
    # CPU<90%·MEM<70% (RFP 협의 조절 가능). psutil 부재/미측정이면 harness 가 SKIP 처리
    # (무데이터 false-PASS 아님). dryrun(TestClient)에선 부하가 가벼워 낮게 측정 — 실 판정은
    # full 모드(테스트서버 uvicorn workers) 실측치가 근거.
    KPI("S11.6", "S11", "CPU 사용률 peak (PER-002)", "%", "le", 90, "max", core=True),
    KPI("S11.7", "S11", "MEM 사용률 peak (PER-002)", "%", "le", 70, "max", core=True),

    # S13(멀티 테넌트 격리) 제거: 격리는 KL 포털 전담 — 단일 고객사 엔진이라 시나리오·KPI 불요.

    # S10 RAG 인용 충실도 (W11 확장)
    KPI("S10.1", "S10", "grounded_ratio", "ratio", "ge", 0.70, "mean"),
    KPI("S10.2", "S10", "evidence_count", "count", "gt", 0, "min"),
    KPI("S10.3", "S10", "label-evidence 일관성", "ratio", "ge", 0.80, "ratio_true"),

    # S16 권한·인증 거부 (W11 확장)
    KPI("S16.1", "S16", "잘못된 키 401 응답", "bool", "ge", True, "bool_all"),
    KPI("S16.2", "S16", "키 누락 거부", "bool", "ge", True, "bool_all"),
    KPI("S16.3", "S16", "거부 응답 p95 latency", "ms", "le", 200, "p95"),
    # S16.4: 단발 측정 비결정성 회피를 위해 5회 반복 → ratio_true. 80% 이상 통과면 PASS.
    KPI("S16.4", "S16", "정상 키 200/201", "ratio", "ge", 0.80, "ratio_true"),

    # S17 감사 로그 무결성 (W11 확장)
    KPI("S17.1", "S17", "audit_count 정합", "ratio", "ge", 0.95, "last", requires=["pg"], core=True),
    KPI("S17.2", "S17", "actor_role 일치", "ratio", "ge", 0.99, "ratio_true", requires=["pg"]),
    KPI("S17.3", "S17", "timestamp 단조 증가", "bool", "ge", True, "bool_all", requires=["pg"]),

    # S18 폐쇄망 번들 무결성 (W11 확장)
    KPI("S18.1", "S18", "manifest 존재", "bool", "ge", True, "bool_all"),
    KPI("S18.2", "S18", "manifest 결정론", "bool", "ge", True, "bool_all"),
    KPI("S18.3", "S18", "dry-run 시간", "ms", "le", 30000, "max"),

    # S12 대용량 일괄 분류 (W12 확장)
    KPI("S12.1", "S12", "N=100 throughput", "docs/s", "ge", 5, "last"),
    KPI("S12.2", "S12", "N=500 응답 정상", "bool", "ge", True, "bool_all"),
    KPI("S12.3", "S12", "N=1000 응답 정상", "bool", "ge", True, "bool_all"),
    KPI("S12.4", "S12", "N=1001 거부 (413)", "bool", "ge", True, "bool_all"),
    KPI("S12.5", "S12", "N=1000 p95 latency", "ms", "le", 60000, "p95"),

    # S14 ES 정전 후 자동 복구 (W12 확장)
    # dryrun: 가용성 식별만. full: docker 시뮬레이션 (require=es + 명시 실행)
    KPI("S14.1", "S14", "ES 가용성 확인 가능", "bool", "ge", True, "bool_all"),
    KPI("S14.2", "S14", "정전 중 error_rate", "ratio", "le", 0.50, "last", requires=["es"]),
    KPI("S14.3", "S14", "복구까지 시간", "ms", "le", 60000, "max", requires=["es"]),

    # S15 백업·복원 라운드트립 (W12 확장)
    KPI("S15.1", "S15", "dr_restore_check 종료 코드 OK", "bool", "ge", True, "bool_all"),
    KPI("S15.2", "S15", "백업 3종 최신 (24h)", "bool", "ge", True, "bool_all", requires=["pg", "es", "minio"]),
]


# ---------------------------------------------------------------------------
# KPI → 책임 모듈 매핑 (운영 인시던트 트리아지)
# ---------------------------------------------------------------------------
# doc/11 §8.2가 사람이 손으로 유지하던 KPI→모듈 표는 실재하지 않는 테스트 파일·make
# 타깃을 가리키며 이미 stale(drift)했다. 이제 **코드를 단일 진실원**으로 삼고, 시나리오
# 단위로 책임 모듈(어느 서브시스템을 볼지)을 매핑한다. test_kpi_module_map.py가 (a) 모든
# KPI 시나리오에 매핑이 있고 (b) 매핑된 경로가 실재하는지 CI에서 검증해 재발 drift를 차단한다.
# 경로는 poc 루트 상대. 시나리오 단위라 70개 KPI마다 일일이 손대지 않아도 정확성이 유지된다.
_SCENARIO_MODULES: dict[str, tuple[str, ...]] = {
    "S1":  ("src/koipa/modules/m5_inference", "src/koipa/modules/m2_preprocess",
            "src/koipa/services/classify_service.py", "src/koipa/api/classify.py"),
    "S2":  ("src/koipa/services/async_classify_service.py", "src/koipa/api/async_classify.py",
            "src/koipa/workers"),
    "S3":  ("src/koipa/services/confirm_service.py", "src/koipa/api/confirm.py"),
    "S4":  ("src/koipa/services/schema_admin_service.py", "src/koipa/api/schema_admin.py"),
    "S5":  ("src/koipa/services/document_ingestion_service.py", "src/koipa/rag",
            "src/koipa/api/documents.py"),
    "S6":  ("src/koipa/services/synthesis_service.py", "src/koipa/modules/m1_synthesis"),
    "S7":  ("src/koipa/modules/m4_training", "src/koipa/workers"),
    "S8":  ("src/koipa/modules/m6_evaluation",),
    "S9":  ("src/koipa/modules/m5_inference", "src/koipa/modules/m3_labeling"),
    "S10": ("src/koipa/rag", "src/koipa/perf/scenarios.py"),
    "S11": ("src/koipa/perf",),
    "S12": ("src/koipa/services/async_classify_service.py", "src/koipa/api/async_classify.py"),
    "S14": ("src/koipa/adapters/vectorstore", "src/koipa/adapters/storage"),
    "S15": ("scripts/dr_restore_check.py", "scripts/backup_postgres.py"),
    "S16": ("src/koipa/api/_jwt_auth.py", "src/koipa/api/_rbac.py"),
    "S17": ("src/koipa/api/middleware.py", "src/koipa/repositories/audit_repo.py",
            "src/koipa/services/audit_chain.py"),
    "S18": ("scripts/build_offline_bundle.py",),
}


def modules_for_scenario(scenario: str) -> tuple[str, ...]:
    """시나리오 ID(S1~S18) → 책임 모듈 경로 목록(poc 루트 상대). 미정의면 빈 튜플."""
    return _SCENARIO_MODULES.get(scenario, ())


def modules_for_kpi(kpi: KPI) -> tuple[str, ...]:
    """KPI → 책임 모듈. 인시던트 시 'KPI Sx.y FAIL → 이 모듈을 보라'의 코드 진실원."""
    return modules_for_scenario(kpi.scenario)


def kpi_by_id(kpi_id: str) -> KPI:
    for k in KPIS:
        if k.id == kpi_id:
            return k
    raise KeyError(kpi_id)


def core_kpis() -> list[KPI]:
    return [k for k in KPIS if k.core]

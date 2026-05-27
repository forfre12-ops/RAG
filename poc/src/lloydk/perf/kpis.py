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
    KPI("S2.1", "S2", "async 202 latency", "ms", "le", 200, "p95"),
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
    # Recall@5는 실 ES + 학습된 모델(또는 풀 임베딩) 전제. dryrun(hash 임베딩)에선 SKIP.
    KPI("S5.4", "S5", "Recall@5", "ratio", "ge", 0.80, "last", requires=["es", "trained_model"], core=True),
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

    # S11 부하 시나리오 (W10 확장)
    KPI("S11.1", "S11", "동시 50 error_rate", "ratio", "le", 0.01, "last"),
    KPI("S11.2", "S11", "동시 50 p95 latency", "ms", "le", 8000, "p95"),
    KPI("S11.3", "S11", "throughput", "req/s", "ge", 5, "last"),

    # S13 멀티 테넌트 격리 (W10 확장)
    # bool/count 핵심 격리 검증. 데이터 누출 사고 방지 KPI
    KPI("S13.1", "S13", "교차 노출 횟수", "count", "le", 0, "max", core=True),
    KPI("S13.2", "S13", "audit tenant_id 정합", "ratio", "ge", 0.99, "ratio_true", requires=["pg"]),
    KPI("S13.3", "S13", "가이드 인덱스 분리", "bool", "ge", True, "bool_all"),

    # S10 RAG 인용 충실도 (W11 확장)
    KPI("S10.1", "S10", "grounded_ratio", "ratio", "ge", 0.70, "mean"),
    KPI("S10.2", "S10", "evidence_count", "count", "gt", 0, "min"),
    KPI("S10.3", "S10", "label-evidence 일관성", "ratio", "ge", 0.80, "ratio_true"),

    # S16 권한·인증 거부 (W11 확장)
    KPI("S16.1", "S16", "잘못된 키 401 응답", "bool", "ge", True, "bool_all"),
    KPI("S16.2", "S16", "키 누락 거부", "bool", "ge", True, "bool_all"),
    KPI("S16.3", "S16", "거부 응답 p95 latency", "ms", "le", 200, "p95"),
    KPI("S16.4", "S16", "정상 키 200/201", "bool", "ge", True, "bool_all"),

    # S17 감사 로그 무결성 (W11 확장)
    KPI("S17.1", "S17", "audit_count 정합", "ratio", "ge", 0.95, "last", requires=["pg"], core=True),
    KPI("S17.2", "S17", "actor_role 일치", "ratio", "ge", 0.99, "ratio_true", requires=["pg"]),
    KPI("S17.3", "S17", "timestamp 단조 증가", "bool", "ge", True, "bool_all", requires=["pg"]),

    # S18 폐쇄망 번들 무결성 (W11 확장)
    KPI("S18.1", "S18", "manifest 존재", "bool", "ge", True, "bool_all"),
    KPI("S18.2", "S18", "manifest 결정론", "bool", "ge", True, "bool_all"),
    KPI("S18.3", "S18", "dry-run 시간", "ms", "le", 30000, "max"),
]


def kpi_by_id(kpi_id: str) -> KPI:
    for k in KPIS:
        if k.id == kpi_id:
            return k
    raise KeyError(kpi_id)


def core_kpis() -> list[KPI]:
    return [k for k in KPIS if k.core]

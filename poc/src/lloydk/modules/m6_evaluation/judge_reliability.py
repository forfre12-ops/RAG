"""심판(LLM) 신뢰도 폭로 리포트 — 출처별 층화 confusion (라벨 미수정).

설계 결정(평가셋 앵커 중심 다층 평가, 2026-06-29):
- 합의 holdout 천장은 'LLM이 LLM을 채점'하는 순환이라 부풀려진다. 이 모듈은 두 심판
  (예: Qwen 기존 라벨 a vs Claude 재판정 b)의 불일치를 **출처/도메인별로 층화**해
  "이 심판은 이 출처에서 얼마나 못 믿는지"를 신뢰구간으로 폭로한다.
- **라벨을 고치지 않는다**(보정 아님 · 폭로만). 입력 레코드는 변경하지 않는다.
- 591건 실측: Claude가 공개판례 출처에서 대거 하향(고등급 0건). 단 공개판례는 비공지성상
  S3가 맞을 수 있어 '편향'으로 단정하지 않는다 → proxy성 출처의 하향엔 '정정일 수도/
  편향일 수도'라는 양가(proxy_caveat) 플래그를 단다. (해석은 사람이 한다.)

순수 함수(레코드 주입) + 얇은 jsonl 로더. eval_cards.wilson_interval 재사용.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from lloydk.modules.m6_evaluation.eval_cards import GRADE_ORDER, wilson_interval

# 출처/도메인 값에 이 표지가 들어가면 'proxy(공개·판례)성'으로 보고 하향 양가 플래그.
DEFAULT_PROXY_MARKERS = ("판례", "판결", "공개", "공시", "public", "court")
# 층화 표본이 이보다 적으면 신뢰구간만 넓게 — 결론 단정 금지.
DEFAULT_MIN_STRATUM_N = 10
# 이 이상 하향(b가 a보다 낮은 등급)이면 systematic_downgrade 플래그.
DEFAULT_DOWNGRADE_FLAG = 0.5

FLAG_SYSTEMATIC_DOWNGRADE = "systematic_downgrade"
FLAG_SYSTEMATIC_UPGRADE = "systematic_upgrade"
FLAG_HIGH_DISAGREEMENT = "high_disagreement"
FLAG_PROXY_CAVEAT = "proxy_caveat"  # 하향이 정정(공개→S3)일 수도 = 편향 단정 금지
FLAG_LOW_N = "low_n"


@dataclass
class Stratum:
    """한 층(예: source=판례)의 두 심판 비교."""

    key: str
    n: int
    agreement_rate: float
    agreement_ci: tuple[float, float]
    downgrade_rate: float       # b가 a보다 낮은 등급(덜 비밀)으로 본 비율
    upgrade_rate: float
    a_dist: dict[str, int]
    b_dist: dict[str, int]
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "n": self.n,
            "agreement_rate": round(self.agreement_rate, 4),
            "agreement_ci": [round(self.agreement_ci[0], 4), round(self.agreement_ci[1], 4)],
            "downgrade_rate": round(self.downgrade_rate, 4),
            "upgrade_rate": round(self.upgrade_rate, 4),
            "a_dist": self.a_dist,
            "b_dist": self.b_dist,
            "flags": self.flags,
        }


@dataclass
class JudgeReliabilityReport:
    a_name: str
    b_name: str
    overall: Stratum
    by_source: list[Stratum] = field(default_factory=list)
    by_domain: list[Stratum] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "a_name": self.a_name,
            "b_name": self.b_name,
            "overall": self.overall.to_dict(),
            "by_source": [s.to_dict() for s in self.by_source],
            "by_domain": [s.to_dict() for s in self.by_domain],
            "note": "심판 신뢰도 폭로 — 라벨 미수정. proxy_caveat는 하향이 정정일 수도 있음을 뜻함(편향 단정 금지).",
        }


def _is_proxy(key: str, markers: Sequence[str]) -> bool:
    k = key.lower()
    return any(m.lower() in k for m in markers)


def _stratum(
    key: str,
    rows: list[tuple[str, str]],
    *,
    proxy_markers: Sequence[str],
    min_n: int,
    downgrade_flag: float,
) -> Stratum:
    """rows = [(a_grade, b_grade), ...] (등급 코드만). 한 층의 비교 통계 + 플래그."""
    n = len(rows)
    agree = sum(1 for a, b in rows if a == b)
    # GRADE_ORDER: 낮을수록 고등급. b_order > a_order = b가 더 낮은 등급(덜 비밀) = 하향.
    down = sum(1 for a, b in rows if a in GRADE_ORDER and b in GRADE_ORDER and GRADE_ORDER[b] > GRADE_ORDER[a])
    up = sum(1 for a, b in rows if a in GRADE_ORDER and b in GRADE_ORDER and GRADE_ORDER[b] < GRADE_ORDER[a])
    a_dist = dict(Counter(a for a, _ in rows))
    b_dist = dict(Counter(b for _, b in rows))
    agreement_rate = agree / n if n else 0.0
    downgrade_rate = down / n if n else 0.0
    upgrade_rate = up / n if n else 0.0

    flags: list[str] = []
    if n < min_n:
        flags.append(FLAG_LOW_N)
    if downgrade_rate >= downgrade_flag:
        flags.append(FLAG_SYSTEMATIC_DOWNGRADE)
    if upgrade_rate >= downgrade_flag:
        flags.append(FLAG_SYSTEMATIC_UPGRADE)
    if n and agreement_rate < 0.5:
        flags.append(FLAG_HIGH_DISAGREEMENT)
    # proxy성 출처에서 하향이 보이면 '정정일 수도' 양가 플래그(편향 단정 금지).
    if down and _is_proxy(key, proxy_markers):
        flags.append(FLAG_PROXY_CAVEAT)

    return Stratum(
        key=key,
        n=n,
        agreement_rate=agreement_rate,
        agreement_ci=wilson_interval(agree, n),
        downgrade_rate=downgrade_rate,
        upgrade_rate=upgrade_rate,
        a_dist=a_dist,
        b_dist=b_dist,
        flags=flags,
    )


def build_judge_reliability(
    records: Sequence[dict],
    *,
    a_of: Callable[[dict], object] = lambda r: r.get("qwen_grade"),
    b_of: Callable[[dict], object] = lambda r: r.get("claude_grade"),
    source_of: Callable[[dict], object] = lambda r: r.get("source") or "?",
    domain_of: Callable[[dict], object] = lambda r: r.get("domain") or "?",
    a_name: str = "qwen",
    b_name: str = "claude",
    proxy_markers: Sequence[str] = DEFAULT_PROXY_MARKERS,
    min_stratum_n: int = DEFAULT_MIN_STRATUM_N,
    downgrade_flag: float = DEFAULT_DOWNGRADE_FLAG,
) -> JudgeReliabilityReport:
    """두 심판(a=기존 라벨, b=재판정)의 불일치를 전체·출처별·도메인별로 폭로.

    **라벨을 고치지 않는다** — 입력 records는 읽기만 한다. 등급 코드가 GRADE_ORDER에
    없는 행은 해당 통계에서 안전하게 제외(방향성 카운트 한정).
    """
    all_rows: list[tuple[str, str]] = []
    by_src: dict[str, list[tuple[str, str]]] = defaultdict(list)
    by_dom: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for r in records:
        a = a_of(r)
        b = b_of(r)
        if a is None or b is None:
            continue
        a, b = str(a), str(b)
        all_rows.append((a, b))
        by_src[f"source={source_of(r)}"].append((a, b))
        by_dom[f"domain={domain_of(r)}"].append((a, b))

    def mk(key: str, rows: list[tuple[str, str]]) -> Stratum:
        return _stratum(
            key, rows,
            proxy_markers=proxy_markers, min_n=min_stratum_n, downgrade_flag=downgrade_flag,
        )

    overall = mk("overall", all_rows)
    by_source = [mk(k, v) for k, v in sorted(by_src.items(), key=lambda kv: -len(kv[1]))]
    by_domain = [mk(k, v) for k, v in sorted(by_dom.items(), key=lambda kv: -len(kv[1]))]

    return JudgeReliabilityReport(
        a_name=a_name, b_name=b_name,
        overall=overall, by_source=by_source, by_domain=by_domain,
    )


def load_rejudge_jsonl(path: str | Path) -> list[dict]:
    """rejudged.jsonl(레코드별 qwen_grade·claude_grade·source·domain) 로더. 없으면 []."""
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows

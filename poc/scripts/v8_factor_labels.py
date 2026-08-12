"""요소 라벨 3상태 스키마 — v8 데이터의 정답 규약.

왜 필요한가(실측 2026-08-13). v8 시드 8건이 두 규칙으로 갈려 있었다:

    0001 (2,2,0) -> S1    m=0 을 '입증된 부재'로 읽음   grade_from_svm
    0005 (1,2,0) -> S2    m=0 을 '미언급'으로 읽음      grade_from_svm_floored
    0008 (1,0,2) -> S2    v=0 을 '미언급'으로 읽음      floored

원인은 `expected_factor_scores` 의 0 이 **proven_absent 와 unknown 을 동시에** 뜻하는 것이다.
본문에는 그 구분이 없으므로 모델이 배울 수 없는 노이즈다. 8건에서 이미 갈렸으니 수천 건으로
늘리면 그대로 증식한다.

정본 doc/22 §3.6 (rule_engine.py:86) 이 이미 원칙을 갖고 있다:

    요소값 0 은 '근거 없음'이 아니라 공개/무가치/무관리가 *입증*된 경우에만

이 모듈은 그 원칙을 데이터 스키마로 만든다. 등급은 저술자가 적지 않고 상태에서 파생시킨다.
"""
from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: E402

FACTORS = ("secrecy", "value", "management")

# 상태 3종. level 은 present 일 때만 의미가 있다.
PROVEN_ABSENT = "proven_absent"
PRESENT = "present"
UNKNOWN = "unknown"
STATES = (PROVEN_ABSENT, PRESENT, UNKNOWN)

# 근거 방향 · 유형 · 검증상태
DIRECTIONS = ("존재", "부재", "불명")
KINDS = ("본문진술", "문서메타데이터", "외부검증")
VERIFIED = ("텍스트상주장", "시스템확인")


@dataclass
class FactorLabel:
    """요소 하나의 정답. 근거 없이 상태만 있는 라벨은 만들지 않는다."""

    state: str
    level: int | None = None          # present 일 때만 1 또는 2
    span: str = ""                    # 근거 문장 원문
    direction: str = "불명"
    kind: str = "본문진술"
    verified: str = "텍스트상주장"
    reason: str = ""                  # 판단 사유 코드

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise ValueError(f"state 는 {STATES} 중 하나여야 한다: {self.state}")
        if self.state == PRESENT:
            if self.level not in (1, 2):
                raise ValueError("present 는 level 1 또는 2 를 가져야 한다")
        else:
            if self.level not in (None, 0):
                raise ValueError(f"{self.state} 는 level 을 갖지 않는다")
            self.level = None
        if self.state in (PROVEN_ABSENT, PRESENT) and not self.span:
            raise ValueError(f"{self.state} 는 근거 span 이 필요하다 — 근거 없는 단정 금지")
        if self.state == UNKNOWN and self.span:
            raise ValueError("unknown 에 span 이 있으면 unknown 이 아니다")
        if self.direction not in DIRECTIONS:
            raise ValueError(f"direction: {self.direction}")
        if self.kind not in KINDS:
            raise ValueError(f"kind: {self.kind}")
        if self.verified not in VERIFIED:
            raise ValueError(f"verified: {self.verified}")

    # 등급 산출에 쓰는 정수값. proven_absent 만 0 이다.
    def score(self) -> int:
        return 0 if self.state == PROVEN_ABSENT else int(self.level or 0)

    # 보수적 완성 — unknown 을 최악값으로 채운다.
    def worst_case(self) -> int:
        if self.state == PROVEN_ABSENT:
            return 0
        if self.state == UNKNOWN:
            return 2
        return int(self.level or 0)


@dataclass
class DocumentLabel:
    """문서 하나의 정답. 등급은 적지 않고 파생시킨다."""

    secrecy: FactorLabel
    value: FactorLabel
    management: FactorLabel
    meta: dict = field(default_factory=dict)

    def _triple(self, *, worst: bool) -> tuple[int, int, int]:
        f = (self.secrecy, self.value, self.management)
        return tuple(x.worst_case() if worst else x.score() for x in f)  # type: ignore[return-value]

    def grade(self) -> str:
        """저술 의도 등급. unknown 은 0 으로 본다(요소가 실제로 없는 문서)."""
        return grade_from_svm(*self._triple(worst=False))

    def grade_worst_case(self) -> str:
        """unknown 을 최악으로 채웠을 때의 등급. S3 자동확정 판정용."""
        return grade_from_svm(*self._triple(worst=True))

    def s3_provable(self) -> bool:
        """S3-입증형인가 — 임의 라벨이 아니라 결정 규칙에서 파생된다.

        unknown 을 전부 최악값으로 채우고도 S3 면, 어떤 해석에서도 S3 다.
        """
        return self.grade_worst_case() == "S3"

    def s3_kind(self) -> str | None:
        if self.grade() != "S3":
            return None
        return "S3-입증형" if self.s3_provable() else "S3-무신호형"


# ─────────────────────────────────────────────────────────────────────────────
# 규칙에서 파생되는 사실들 — 데이터 설계가 여기 의존한다
# ─────────────────────────────────────────────────────────────────────────────

def grade_distribution_over_combos() -> dict[str, list[str]]:
    """27개 S/V/M 조합이 등급으로 어떻게 떨어지는가.

    균등 표집을 하면 안 되는 이유다 — 18/27 이 S3 라 학습셋이 S3 67% 가 된다.
    """
    out: dict[str, list[str]] = {}
    for s, v, m in itertools.product(range(3), repeat=3):
        out.setdefault(grade_from_svm(s, v, m), []).append(f"{s}{v}{m}")
    return out


def adjacent_boundaries() -> list[tuple[str, str, str, str]]:
    """한 요소를 1단계만 바꿨을 때 등급이 넘어가는 인접 경계.

    counterfactual 쌍의 예산은 전부 여기 쓴다. (from, to, from_grade, to_grade)
    """
    seen: set[tuple[tuple[int, ...], tuple[int, ...]]] = set()
    edges: list[tuple[str, str, str, str]] = []
    for combo in itertools.product(range(3), repeat=3):
        for i in range(3):
            for d in (-1, 1):
                nb = list(combo)
                nb[i] += d
                if not 0 <= nb[i] <= 2:
                    continue
                nbt = tuple(nb)
                key = (combo, nbt) if combo < nbt else (nbt, combo)
                if key in seen:
                    continue
                seen.add(key)
                g1, g2 = grade_from_svm(*combo), grade_from_svm(*nbt)
                if g1 != g2:
                    edges.append(("".join(map(str, combo)), "".join(map(str, nbt)), g1, g2))
    return edges


def s3_provable_states() -> list[tuple[str, str, str]]:
    """레벨을 모르고 **상태만** 알 때, 보수적 완성 후에도 S3 인 라벨 상태.

    present 의 레벨이 미상이므로 최악값 2 로 본다. 실측 15개이며 전부 secrecy 또는
    value 가 proven_absent 다 — 상태만 아는 단계에서 management 부재는 기여가 없다.
    """
    w = {PROVEN_ABSENT: 0, PRESENT: 2, UNKNOWN: 2}
    out = []
    for s, v, m in itertools.product(STATES, repeat=3):
        if grade_from_svm(w[s], w[v], w[m]) == "S3":
            out.append((s, v, m))
    return out


def s3_requires() -> str:
    """S3-입증형의 필요조건을 규칙에서 유도한다.

    두 층위를 구분해야 한다 — 데이터 설계를 좁게 잡으면 안 된다:

      상태만 알 때   secrecy 또는 value 가 proven_absent 여야 한다
      레벨까지 알 때  management proven_absent 도 성립한다. 단 s 또는 v 가 2 미만으로
                     **확정**됐을 때만이다. 둘 다 2 면 (2,2,0) -> S1 이라 S3 가 아니다
    """
    states = s3_provable_states()
    only_sv = all(s == PROVEN_ABSENT or v == PROVEN_ABSENT for s, v, _ in states)
    mgmt_only = [t for t in states if t[2] == PROVEN_ABSENT and t[0] != PROVEN_ABSENT and t[1] != PROVEN_ABSENT]
    assert only_sv and not mgmt_only, "규칙이 바뀌었다 — 데이터 설계를 다시 봐야 한다"
    # 레벨을 아는 경우의 반례가 실제로 존재하는지 확인한다(설계 문장을 좁게 쓰지 않기 위해).
    assert grade_from_svm(1, 2, 0) == "S3" and grade_from_svm(2, 2, 0) == "S1"
    return (
        "상태만 알 때: S3-입증형 <=> secrecy 또는 value 가 proven_absent\n"
        "  레벨까지 알 때: management proven_absent 도 가능 — 단 s 또는 v 가 2 미만 확정 시에만"
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    dist = grade_distribution_over_combos()
    print("27조합 -> 등급")
    for g in ("TS", "S1", "S2", "S3"):
        print(f"  {g:3s} {len(dist.get(g, [])):2d}개  {dist.get(g, [])}")
    print(f"\n  균등 표집시 S3 비율 {len(dist['S3'])/27:.1%} — 그래서 조합 균등 표집은 금지")

    edges = adjacent_boundaries()
    print(f"\n인접 경계 {len(edges)}개 — counterfactual 예산은 전부 여기")
    by: dict[str, list[str]] = {}
    for a, b, g1, g2 in edges:
        by.setdefault(f"{g1}<->{g2}", []).append(f"{a}-{b}")
    for k, v in sorted(by.items(), key=lambda x: -len(x[1])):
        print(f"  {k:9s} {len(v):2d}개  {v}")

    print(f"\n보수적 완성 후 S3 인 라벨상태 {len(s3_provable_states())}개")
    print(f"  {s3_requires()}")

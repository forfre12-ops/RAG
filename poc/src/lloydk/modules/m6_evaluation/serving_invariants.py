"""E — 서빙 메타모픽 불변식 체커: 출처 cap '조용한 미탐' + 메타데이터 floor 위반 (정답 불요).

설계 결정(평가셋 앵커 중심 다층 평가, 2026-06-29):
- 서빙 파이프라인(m5_inference/pipeline.py)은 공개 출처(판례·보도자료·공개특허…)면 등급을 S3로
  cap한다. 그런데 cap이 **고등급(TS/S1) 콘텐츠를 덮을 때**는 반드시 cap-conflict 경고 →
  needs_review 라우팅을 남긴다(pipeline.py:339-343). 그 경고가 빠지면 = **내부 비밀의 출처
  오태깅이 S3로 조용히 새는 미탐(silent miss)**.
- 이 모듈은 그 불변식을 **정답 라벨 없이** 검사한다(메타모픽: 입력의 논리적 성질):
    · silent_cap — 공개 출처 cap이 고등급 콘텐츠를 하향시켰는데 검수 신호 부재 → 위반(FN 위험)
    · floor      — security_marking이 가리키는 등급이 최종보다 높은데 상향 안 됨 → 위반(under-floor)
  **실패만 정보**(D와 동일): quality_score는 NotImplementedError.

pipeline.py를 *변경하지 않는다* — 서빙 출력(등급·출처·검수신호)을 ServingCase로 매핑해 받는
순수 체커다. 라이브 파이프라인 배선(서빙 트랙 E1/E2)과 독립. eval_cards.wilson_interval 재사용.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from lloydk.modules.m6_evaluation.eval_cards import GRADE_ORDER, wilson_interval
from lloydk.modules.m6_evaluation.metamorphic import ViolationStat

# 고등급(미탐 시 가장 치명적) — cap-conflict 경고가 의무인 등급.
UPPER = ("TS", "S1")


@dataclass
class ServingCase:
    """서빙 결과 1건의 메타모픽 검사 입력(파이프라인 출력에서 매핑).

    content_grade : cap 전 콘텐츠/룰 신호 등급(InferenceResult.rule_grade 등).
    final_grade   : 서빙 최종 등급.
    source_public : 메타데이터 출처가 공개(판례·보도자료·공개특허·공시·채용공고 …).
    has_review_signal : cap-conflict / needs_review / metadata-access-conflict 등 검수 라우팅 신호 존재.
    marking_grade : security_marking이 가리키는 등급(TS/S1/S2). 없으면 None.
    """

    content_grade: str
    final_grade: str
    source_public: bool = False
    has_review_signal: bool = False
    marking_grade: str | None = None
    case_id: str | None = None


@dataclass
class ServingInvariantReport:
    silent_cap: ViolationStat
    floor: ViolationStat
    silent_cap_failures: list[dict] = field(default_factory=list)

    @property
    def quality_score(self):
        """통과=품질 아님 — 실패(위반)만 정보(D와 동일 규율)."""
        raise NotImplementedError(
            "서빙 불변식은 실패(위반)만 정보다. 위반 0이 '안전'을 증명하지 않는다 "
            "(검사 못 한 경로가 있을 수 있음). violations/CI를 보라."
        )

    def to_dict(self) -> dict:
        return {
            "silent_cap": self.silent_cap.to_dict(),
            "floor": self.floor.to_dict(),
            "silent_cap_failures": self.silent_cap_failures,
            "quality_score": None,
            "note": "실패만 정보 · silent_cap=공개출처 cap이 고등급 덮음+검수신호 없음(FN) · floor=표기 상향 누락",
        }


def _ord(g: object) -> int | None:
    return GRADE_ORDER.get(str(g)) if g is not None else None


def build_serving_invariant_report(
    cases: Sequence[ServingCase | dict],
    *,
    content_of: Callable = lambda c: getattr(c, "content_grade", None) if not isinstance(c, dict) else c.get("content_grade"),
    final_of: Callable = lambda c: getattr(c, "final_grade", None) if not isinstance(c, dict) else c.get("final_grade"),
    public_of: Callable = lambda c: getattr(c, "source_public", False) if not isinstance(c, dict) else c.get("source_public", False),
    review_of: Callable = lambda c: getattr(c, "has_review_signal", False) if not isinstance(c, dict) else c.get("has_review_signal", False),
    marking_of: Callable = lambda c: getattr(c, "marking_grade", None) if not isinstance(c, dict) else c.get("marking_grade"),
    id_of: Callable = lambda c: getattr(c, "case_id", None) if not isinstance(c, dict) else c.get("case_id"),
) -> ServingInvariantReport:
    """ServingCase(또는 dict) 목록의 서빙 불변식 위반을 집계. GRADE_ORDER: 낮을수록 고등급.

    silent_cap 위반: 공개 출처(public) AND 콘텐츠가 고등급(TS/S1) AND 최종이 더 낮은 등급으로
      cap(order↑) AND 검수 신호 없음 → 출처 오태깅 미탐이 조용히 새는 경우(pipeline.py 가드 회귀).
    floor 위반: marking 등급이 최종보다 높은 비밀(order↓)인데 최종이 그만큼 상향 안 됨 → under-floor.
    """
    sc_n = sc_v = 0
    failures: list[dict] = []
    fl_n = fl_v = 0

    for c in cases:
        cg, fg = _ord(content_of(c)), _ord(final_of(c))

        # silent_cap — 공개 출처 cap이 고등급 콘텐츠를 덮었는데 검수 신호 부재
        if public_of(c) and str(content_of(c)) in UPPER and cg is not None and fg is not None:
            sc_n += 1
            if fg > cg and not review_of(c):  # 더 낮은 등급으로 cap + 검수 신호 없음
                sc_v += 1
                failures.append({
                    "case_id": id_of(c),
                    "content_grade": str(content_of(c)),
                    "final_grade": str(final_of(c)),
                    "reason": "공개출처 cap이 고등급 콘텐츠 하향 + 검수신호 없음(조용한 미탐)",
                })

        # floor — security_marking이 최종보다 높은 비밀인데 상향 안 됨
        mk = marking_of(c)
        mo = _ord(mk)
        if mo is not None and fg is not None:
            fl_n += 1
            if mo < fg:  # 표기가 더 비밀(order 작음)인데 최종이 더 낮음 → 상향 누락
                fl_v += 1

    silent_cap = ViolationStat(
        name="silent_cap(공개출처 cap 조용한 미탐)", n=sc_n, violations=sc_v,
        violation_rate=(sc_v / sc_n if sc_n else 0.0), ci=wilson_interval(sc_v, sc_n),
        note="공개 출처 cap이 TS/S1 콘텐츠를 덮으면 cap-conflict→needs_review 의무. 위반=조용한 미탐(FN).",
    )
    floor = ViolationStat(
        name="floor(표기 상향 누락)", n=fl_n, violations=fl_v,
        violation_rate=(fl_v / fl_n if fl_n else 0.0), ci=wilson_interval(fl_v, fl_n),
        note="security_marking이 최종보다 높은 비밀이면 그 등급으로 상향(floor)해야. 위반=under-floor.",
    )
    return ServingInvariantReport(silent_cap=silent_cap, floor=floor, silent_cap_failures=failures)

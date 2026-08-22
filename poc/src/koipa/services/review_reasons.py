"""검수 라우팅의 **실제 원인**을 경고 문자열에서 되짚는다.

왜 필요한가(실측 2026-08-22). 서빙 측정이 검수 사유를 `warnings` 를 ':' 로 잘라 앞부분을
세는 방식으로 집계했다(measure_serving_fnr.py 종전 구현). hardened42 42건 집계에서
`persistence skipped` 가 15건으로 1위처럼 올라왔는데, 그건 doc_id 가 비-UUID 라 DB 에
안 남겼다는 **기록 경고**이지 상태 판정에 관여하지 않는다. 42건 전부에 붙는 경고라
needs_review 15건 전부에도 붙었을 뿐이다. 사유 분포는 "무엇을 고쳐야 자동확정이 오르나"를
정하는 표인데, 1위가 판정과 무관한 값이면 그 판단이 틀린다.

원인은 하나로 정해진다. 검수 라우팅 게이트는 `ClassifyService.classify()` 안에 **순서대로**
놓여 있고, 앞 게이트가 status 를 needs_review 로 만들면 뒤 게이트는
`if status != "needs_review"` 가드에 걸려 **평가 자체가 되지 않는다**. 그러므로 한 문서의
검수 사유는 순서상 **조건이 처음 성립한 게이트 하나**다. 이 모듈은 그 순서와 판정 표식을
한 곳에 적어 둔다.

주의. 표식 문자열은 classify_service 가 실제로 보는 것과 같아야 한다 —
`tests/test_review_reasons.py` 가 classify_service.py 원문에 각 표식이 그대로 있는지 확인한다.
게이트를 추가·개명하면 그 테스트가 먼저 깨진다. 어느 표식에도 안 걸린 needs_review 는
조용히 버리지 않고 `UNMAPPED` 로 남긴다 — 표가 코드보다 뒤처졌다는 신호다.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Sequence

# 표에 없는 게이트가 생겼을 때 쓰는 표시. 0 이 아니면 이 모듈을 갱신해야 한다.
UNMAPPED = "unmapped"

# (태그, 판정 표식) — **classify_service.classify() 의 평가 순서 그대로**.
# 표식은 그 게이트의 조건이 성립했을 때 warnings 에 반드시 들어 있는 문자열이다.
# 한 경고 안에 표식이 **전부** 들어 있어야 그 게이트로 친다(icd 처럼 두 조각이 필요한 경우).
REVIEW_GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # classify_service.py:341  conf < review_confidence_threshold
    ("low-confidence", ("low-confidence:",)),
    # :352  ingestion 열화추출 격리(review_flagged)
    ("ingestion-degraded", ("document flagged at ingestion",)),
    # :361  출처 cap 이 TS/S1 상향 신호를 덮음
    ("cap-conflict", ("cap-conflict",)),
    # :371  룰-폴백 자동확정이 빈약한 근거에 기댐
    ("sparse-evidence", ("sparse-evidence",)),
    # :382  영문 약어 부스트만으로 고등급 승격
    ("abbrev-only-escalation", ("abbrev-only-escalation",)),
    # :397  판정할 본문이 없음(구형 HWP 등)
    ("body-below-threshold", ("body_below_classifiable_threshold",)),
    # :406  ICD 접근범위 제한 vs 낮은 예측
    ("metadata-access-conflict", ("metadata-access-conflict",)),
    # :416  M=0 부재입증 vs 비공개 예측
    ("metadata-management-conflict", ("metadata-management-conflict",)),
    # :441  ICD 규약 밖 값 중 **상향 게이트 입력**(미탐 방향)만 라우팅
    ("icd-metadata-fnr-risk", ("icd-metadata-unknown", "미탐 위험")),
    # :505  S3 예측인데 내부/비공개 신호
    ("s2-underclass-risk", ("s2-underclass-risk",)),
    # :519  미탐 방향 게이트가 예외로 미적용
    ("gate-fail-open", ("gate-fail-open",)),
    # :526  룰·모델 불일치(룰 무근거면 abstain — classify_service.py:698)
    ("agreement-gate", ("agreement-gate",)),
    # :537  LLM 2차의견이 더 높은 등급 제시
    ("llm-secondopinion", ("llm-secondopinion",)),
    # :546  kill-gate tripped 중 고등급 자동확정 억제
    ("kill-gate-brake", ("kill-gate-brake",)),
    # :555  사람검증 유사문서가 더 높은 등급
    ("similarity-escalation", ("similarity-escalation",)),
)

REVIEW_GATE_TAGS: tuple[str, ...] = tuple(tag for tag, _ in REVIEW_GATES)


def _matches(warnings: Sequence[str], markers: tuple[str, ...]) -> bool:
    """한 경고 안에 표식이 전부 들어 있으면 True."""
    return any(all(m in str(w) for m in markers) for w in warnings)


def gate_hits(warnings: Iterable[str] | None) -> list[str]:
    """조건이 성립한 게이트 태그 전부 — **게이트 순서대로**.

    앞 게이트가 이미 라우팅했으면 뒤 게이트는 실제로 평가되지 않았다는 점에 주의.
    이 목록은 '무엇이 겹쳤나'를 보기 위한 것이고, 원인은 causal_review_reason 이다.
    """
    ws = [str(w) for w in (warnings or [])]
    return [tag for tag, markers in REVIEW_GATES if _matches(ws, markers)]


def causal_review_reason(
    warnings: Iterable[str] | None, status: str | None = None
) -> str | None:
    """검수로 보낸 게이트 하나. staging(자동확정)이면 None.

    status 를 주면 needs_review 가 아닌 건에 사유를 붙이지 않는다(집계 오염 방지).
    needs_review 인데 어느 표식에도 안 걸리면 UNMAPPED.
    """
    if status is not None and str(status) != "needs_review":
        return None
    hits = gate_hits(warnings)
    if hits:
        return hits[0]
    return UNMAPPED if status is not None else None


def count_causal_reasons(records: Iterable[dict]) -> dict[str, int]:
    """레코드 목록 → 사유별 건수(건수 내림차순). 문서 하나당 사유 하나만 센다."""
    counter: Counter = Counter()
    for r in records:
        reason = causal_review_reason(r.get("warnings"), r.get("status"))
        if reason is not None:
            counter[reason] += 1
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))

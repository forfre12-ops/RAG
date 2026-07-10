"""고등급 veto — 실 golden 데이터 × 서빙 경로 회귀 (감사 rank3, rank1 의 데이터셋 확장).

rank1(test_veto_regression)은 손수 만든 고등급 텍스트로 raw rule-fallback 을 검증했다. 본 테스트는
커밋된 실 golden holdout(public-source 문서)의 고등급(TS/S1) 행을 **배포 서빙 경로**(ClassifyService,
escalation τ=0.30)로 태워 파일럿 합격선 그대로를 잠근다: 고등급 문서가 S3(공개)로 **자동확정**되면
미탐(veto 위반). 단 escalation 이 검수(needs_review)로 라우팅한 건 안전(자동확정 아님)으로 허용.

핵심 근거: raw rule-fallback 은 실 고등급 23행 중 6건을 S3 로 놓치지만(합성-only 데이터 천장),
서빙 escalation 이 그 6건을 전부 needs_review 로 라우팅해 **자동확정 S3 미탐=0** 을 달성한다.
이 테스트는 그 안전 기계(escalation)가 실 데이터에서 계속 작동하는지 회귀 감시한다. 모델/GPU 불요
(rule-fallback + 서빙 게이트). PG 없으면 영속은 _skip_optional_db_work 로 스킵(분류 결과 불변).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_GOLDEN = (
    Path(__file__).resolve().parents[1]
    / "datasets" / "gold_real" / "holdout_eval.clean.jsonl"
)
_HIGH = {"TS", "S1"}
_SAFE_ROUTED = {"needs_review", "needs_second_review"}


def _high_grade_rows() -> list[dict]:
    if not _GOLDEN.exists():
        return []
    rows = [json.loads(ln) for ln in _GOLDEN.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [r for r in rows if r.get("label") in _HIGH and (r.get("text") or "").strip()]


@pytest.fixture(scope="module")
def _serving(monkeypatch_module):
    from lloydk.config import settings
    from lloydk.schemas.classify import ClassifyRequest
    from lloydk.services.classify_service import ClassifyService

    # 배포 서빙 운영점: escalation τ=0.30 (onprem-local/full-train 프로파일 값) 고정.
    monkeypatch_module.setattr(settings, "classifier_escalation_tau", 0.30, raising=False)
    return ClassifyService(), ClassifyRequest


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


def test_golden_high_grade_never_auto_confirmed_s3(_serving):
    """실 golden 고등급 행이 서빙 경로에서 S3 로 자동확정(veto 미탐)되지 않는다."""
    svc, ClassifyRequest = _serving
    rows = _high_grade_rows()
    if len(rows) < 5:
        pytest.skip(f"golden holdout 고등급 행 부족({len(rows)}) — 데이터셋 미비")

    auto_s3_miss: list[str] = []
    s3_routed = 0
    for i, r in enumerate(rows):
        resp = svc.classify(ClassifyRequest(doc_id=f"veto-golden-{i}", content=r["text"], return_evidence=False))
        pred = resp.label.value if hasattr(resp.label, "value") else str(resp.label)
        if pred == "S3":
            if resp.status in _SAFE_ROUTED:
                s3_routed += 1
            else:
                auto_s3_miss.append(f"{r.get('label')}#{i}")

    # 안전 기계 확증: escalation 이 raw 미탐을 검수로 라우팅 → 자동확정 S3 미탐 0.
    assert not auto_s3_miss, (
        f"고등급 미탐(veto): {len(auto_s3_miss)}건이 S3 로 자동확정됨 {auto_s3_miss} "
        f"(escalation 라우팅 실패 — 서빙 안전게이트 회귀). S3-but-routed(safe)={s3_routed}"
    )

"""검수자가 거부한 후보는 기록으로 남고, 학습·평가에서 빠진다.

왜(2026-08-18, WP-B3 + E2-6).

종전 apply_signoff 는 decision=="reject" 를 **그냥 건너뛰기만** 했다. 거부분이 어떤 파일에도
남지 않아 두 가지가 깨졌다.

    ① 그 후보는 build_<id>.jsonl 안에 review_status="gold_candidate" 로 그대로 있어
       tier_of 가 candidate 를 돌려주고 TRAIN_TIERS 에 들어가 **계속 학습에 쓰였다.**
       사람이 "이건 아니다" 라고 판단한 문서가 학습 연료로 남는다는 뜻이다.
    ② 다음 세션에서 '아직 안 본 것' 과 '보고 거부한 것' 을 구분할 수 없어, 남은 건수가
       항상 과대값이었다.

tier_of 에서 잡는 이유: train_records·eval_records·partition_by_tier 가 모두 그것을 거친다.
소비 스크립트 4개(build_p1_holdout_split·build_p1_retrain_dataset·build_p1_v5_clean·
train_eval_synth_silver)를 각각 고칠 필요가 없다.
"""

from __future__ import annotations

import json
from pathlib import Path

from koipa.golden_builder import LabelPair
from koipa.golden_tiers import (
    REJECTED_BY_REVIEWER,
    TIER_HELD,
    eval_records,
    tier_of,
    train_records,
)
from koipa.schemas.common import Actor
from koipa.schemas.golden import GoldenBuildRequest, GoldenSignoffDecision
from koipa.services.golden_build_service import GoldenBuildService

_ACTOR = Actor(user_id="builder1", role="admin")


def _label_fn(text: str) -> LabelPair:
    t = text.lower()
    if "ts문서" in t:
        return LabelPair("TS", 0.9, "TS", 0.95, has_real_evidence=True)
    if "s1문서" in t:
        return LabelPair("S1", 0.9, "S1", 0.95, has_real_evidence=True)
    return LabelPair("S2", 0.8, "S2", 0.9, has_real_evidence=True)


def _job(tmp_path):
    docs = [
        {"doc_id": "a", "text": "ts문서 내용", "source": "판례"},
        {"doc_id": "b", "text": "s1문서 내용", "source": "판례"},
        {"doc_id": "c", "text": "s2문서 내용", "source": "판례"},
    ]
    req = GoldenBuildRequest(source_type="inline", docs=docs, out_dir=str(tmp_path),
                             actor=_ACTOR)
    return GoldenBuildService().submit(req, label_fn=_label_fn).golden_job_id


def _rejected_file(tmp_path, job_id) -> list[dict]:
    p = Path(tmp_path) / f"rejected_{job_id}.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# ── tier 파생 ────────────────────────────────────────────────────────────────

def test_explicit_rejection_is_held_not_candidate():
    base = {"doc_id": "d1", "label": "S2", "review_status": "gold_candidate"}
    assert tier_of(base) == "gold_candidate"
    assert tier_of({**base, "review_status": REJECTED_BY_REVIEWER}) == TIER_HELD
    assert tier_of({**base, "signoff_rejected": {"reviewer_id": "지재원관리자"}}) == TIER_HELD


def test_rejected_records_never_reach_training_or_evaluation():
    keep = {"doc_id": "k", "label": "S2", "review_status": "gold_candidate"}
    drop = {**keep, "doc_id": "d", "review_status": REJECTED_BY_REVIEWER}
    assert [r["doc_id"] for r in train_records([keep, drop])] == ["k"]
    picked, _tier = eval_records([keep, drop])
    assert all(r["doc_id"] != "d" for r in picked)


# ── 서명이 거부를 남긴다 ─────────────────────────────────────────────────────

def test_signoff_persists_rejections(tmp_path):
    job_id = _job(tmp_path)
    out = GoldenBuildService().apply_signoff(
        job_id,
        [GoldenSignoffDecision(doc_id="a", decision="approve"),
         GoldenSignoffDecision(doc_id="c", decision="reject", note="본문이 등급 근거가 안 됨")],
        reviewer_id="지재원관리자", publish=False,
    )
    assert out["rejected_recorded"] == 1
    rows = _rejected_file(tmp_path, job_id)
    assert [r["doc_id"] for r in rows] == ["c"]
    assert rows[0]["review_status"] == REJECTED_BY_REVIEWER
    assert rows[0]["signoff_rejected"]["reviewer_id"] == "지재원관리자"
    assert rows[0]["signoff_rejected"]["note"] == "본문이 등급 근거가 안 됨"
    assert rows[0]["signoff_rejected"]["rejected_at"]


def test_persisted_rejection_is_out_of_training(tmp_path):
    """파일에 남긴 그 레코드가 실제로 학습에서 빠지는지 — 표식만 붙이고 끝나면 의미가 없다."""
    job_id = _job(tmp_path)
    GoldenBuildService().apply_signoff(
        job_id, [GoldenSignoffDecision(doc_id="c", decision="reject", note="근거 부족")],
        reviewer_id="지재원관리자", publish=False,
    )
    rows = _rejected_file(tmp_path, job_id)
    assert train_records(rows) == []


def test_rejections_accumulate_across_sessions(tmp_path):
    """대량 후보는 여러 세션에 나눠 검수한다 — 2세션 제출이 1세션 거부를 지우면 안 된다."""
    job_id = _job(tmp_path)
    svc = GoldenBuildService()
    svc.apply_signoff(job_id, [GoldenSignoffDecision(doc_id="b", decision="reject", note="1회차")],
                      reviewer_id="지재원관리자", publish=False)
    svc.apply_signoff(job_id, [GoldenSignoffDecision(doc_id="c", decision="reject", note="2회차")],
                      reviewer_id="지재원관리자", publish=False)
    assert sorted(r["doc_id"] for r in _rejected_file(tmp_path, job_id)) == ["b", "c"]


def test_changing_your_mind_clears_the_rejection(tmp_path):
    """거부했다가 승인하면 거부 기록이 남아선 안 된다 — 남으면 그 문서는 영원히 held 다."""
    job_id = _job(tmp_path)
    svc = GoldenBuildService()
    svc.apply_signoff(job_id, [GoldenSignoffDecision(doc_id="c", decision="reject", note="오판")],
                      reviewer_id="지재원관리자", publish=False)
    assert [r["doc_id"] for r in _rejected_file(tmp_path, job_id)] == ["c"]

    svc.apply_signoff(job_id, [GoldenSignoffDecision(doc_id="c", decision="approve")],
                      reviewer_id="지재원관리자", publish=False)
    assert _rejected_file(tmp_path, job_id) == [], "최신 판단이 이겨야 한다"


def test_no_rejection_writes_no_noise_file(tmp_path):
    """거부가 하나도 없으면 빈 파일을 만들지 않는다."""
    job_id = _job(tmp_path)
    GoldenBuildService().apply_signoff(
        job_id, [GoldenSignoffDecision(doc_id="a", decision="approve")],
        reviewer_id="지재원관리자", publish=False,
    )
    assert not (Path(tmp_path) / f"rejected_{job_id}.jsonl").exists()

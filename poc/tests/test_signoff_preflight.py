"""서명 전 점검 — 무엇이 막을지 제출하기 **전에** 안다.

왜(2026-08-18, WP-E1·E2). 종전에는 실패가 제출한 뒤에야 드러났다. 검수자가 120건을 다 고르고
제출을 눌러야 "이 이름으로는 서명할 수 없습니다" 나 "publish 경로가 없습니다" 를 봤다.

두 가지를 나눠 담는다.

    blocking   실제로 POST 를 실패시키는 것만. 경고를 섞으면 제출 버튼이 근거 없이 잠긴다.
    warnings   눌러도 되지만 결과가 기대와 다를 것

⚠ 서버는 브라우저의 미제출 결정을 볼 수 없다. 여기 담기는 것은 '서버가 아는 상태' 뿐이고,
  실제로 고른 결정의 검증은 dry_run 이 맡는다(E2-5).

[E1] 배치-스코프 집계도 함께 — 종전 집계는 항상 전량 기준이라 120건짜리 회차만 걸러 봐도
진행률이 306건 기준으로 나왔다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from koipa.golden_builder import LabelPair
from koipa.schemas.common import Actor
from koipa.schemas.golden import GoldenBuildRequest, GoldenSignoffDecision
from koipa.services.golden_build_service import GoldenBuildService
from koipa.services.proxy_gold_candidate_service import (
    TERMINAL_REVIEW_STATUSES,
    normalize_doc_id,
)

_ACTOR = Actor(user_id="builder1", role="admin")


def _label_fn(text: str) -> LabelPair:
    t = text.lower()
    if "ts문서" in t:
        return LabelPair("TS", 0.9, "TS", 0.95, has_real_evidence=True)
    return LabelPair("S2", 0.8, "S2", 0.9, has_real_evidence=True)


def _job(tmp_path):
    docs = [{"doc_id": "a", "text": "ts문서 내용", "source": "판례"},
            {"doc_id": "b", "text": "s2문서 내용", "source": "판례"},
            {"doc_id": "c", "text": "s2문서 또", "source": "판례"}]
    req = GoldenBuildRequest(source_type="inline", docs=docs, out_dir=str(tmp_path),
                             actor=_ACTOR)
    return GoldenBuildService().submit(req, label_fn=_label_fn).golden_job_id


# ── E0: doc_id 정규화 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("GOLD-CAND-TS-ENG-053_적층공정_공정조건표", "GOLD-CAND-TS-ENG-053"),
    ("GOLD-CAND-TS-ENG-053", "GOLD-CAND-TS-ENG-053"),
    ("UPL-2026_a_b", "UPL-2026_a_b"),      # GOLD- 가 아니면 자르지 않는다
    ("", ""),
])
def test_doc_id_normalization_only_trims_gold_prefixed(raw, want):
    """무조건 첫 '_' 앞을 취하면 서로 다른 문서가 한 id 로 뭉친다."""
    assert normalize_doc_id(raw) == want


# ── E1-2: 종결 상태 집합 ────────────────────────────────────────────────────

def test_deferred_counts_as_terminal():
    """보류를 미완료로 세면 1건만 남아도 배치가 영원히 100% 가 안 된다 — 회차를 못 닫는다."""
    assert "deferred" in TERMINAL_REVIEW_STATUSES
    assert "proposed" not in TERMINAL_REVIEW_STATUSES
    assert "under_review" not in TERMINAL_REVIEW_STATUSES


# ── E2-1: preflight ────────────────────────────────────────────────────────

def test_preflight_on_a_missing_job_is_none():
    from uuid import uuid4
    assert GoldenBuildService().signoff_preflight(uuid4()) is None


def test_preflight_is_ok_for_a_fresh_job(tmp_path):
    out = GoldenBuildService().signoff_preflight(_job(tmp_path), reviewer_id="지재원관리자")
    assert out["ok"] is True and out["blocking"] == []
    assert out["candidates"]["total"] == 3
    assert out["candidates"]["remaining"] == 3


def test_preflight_blocks_a_reviewer_who_cannot_sign(tmp_path, monkeypatch):
    """제출을 눌러야 알던 것을 화면 열자마자 안다."""
    from koipa.config import settings
    monkeypatch.setattr(settings, "signoff_default_reviewer", "hong.gildong", raising=False)
    out = GoldenBuildService().signoff_preflight(_job(tmp_path), reviewer_id="hong.gildong")
    assert out["ok"] is False
    codes = [b["code"] for b in out["blocking"]]
    assert "reviewer_rejected" in codes
    assert out["reviewer"]["will_be_rejected"] is True
    assert "SIGNOFF_DEFAULT_REVIEWER" in out["reviewer"]["reason"]


def test_preflight_counts_rejected_as_done_not_remaining(tmp_path):
    """거부한 것을 '남은 것' 으로 세면 회차가 끝나지 않는다(B3 의 기록이 여기서 쓰인다)."""
    job_id = _job(tmp_path)
    svc = GoldenBuildService()
    svc.apply_signoff(job_id, [GoldenSignoffDecision(doc_id="a", decision="approve"),
                               GoldenSignoffDecision(doc_id="b", decision="reject", note="근거 부족")],
                      reviewer_id="지재원관리자", publish=False)
    c = svc.signoff_preflight(job_id, reviewer_id="지재원관리자")["candidates"]
    assert c["already_locked"] == 1 and c["already_rejected"] == 1
    assert c["remaining"] == 1


def test_preflight_warns_when_publish_cannot_land(tmp_path, monkeypatch):
    from koipa.config import settings
    monkeypatch.setattr(settings, "locked_eval_jsonl", "", raising=False)
    out = GoldenBuildService().signoff_preflight(_job(tmp_path), reviewer_id="지재원관리자",
                                                 publish=True)
    assert out["ok"] is True, "경고로 제출을 막으면 안 된다"
    assert "publish_path_missing" in [w["code"] for w in out["warnings"]]


def test_preflight_has_no_side_effects(tmp_path):
    job_id = _job(tmp_path)
    before = sorted(x.name for x in Path(tmp_path).iterdir())
    GoldenBuildService().signoff_preflight(job_id, reviewer_id="지재원관리자")
    assert sorted(x.name for x in Path(tmp_path).iterdir()) == before


# ── E2-5: dry_run ──────────────────────────────────────────────────────────

def test_dry_run_reports_but_writes_nothing(tmp_path):
    job_id = _job(tmp_path)
    before = sorted(x.name for x in Path(tmp_path).iterdir())
    out = GoldenBuildService().apply_signoff(
        job_id,
        [GoldenSignoffDecision(doc_id="a", decision="approve"),
         GoldenSignoffDecision(doc_id="b", decision="reject", note="근거 부족")],
        reviewer_id="지재원관리자", publish=False, dry_run=True,
    )
    assert out["dry_run"] is True
    assert out["locked"] == 1, "판정은 그대로 돌아야 미리보기가 쓸모 있다"
    assert sorted(x.name for x in Path(tmp_path).iterdir()) == before, "파일을 만들었다"


def test_dry_run_does_not_publish(tmp_path, monkeypatch):
    live = Path(tmp_path) / "live.jsonl"
    from koipa.config import settings
    monkeypatch.setattr(settings, "locked_eval_jsonl", str(live), raising=False)
    out = GoldenBuildService().apply_signoff(
        _job(tmp_path), [GoldenSignoffDecision(doc_id="a", decision="approve")],
        reviewer_id="지재원관리자", publish=True, dry_run=True,
    )
    assert out["published"] is False and not live.exists()
    assert "dry_run" in (out["publish_note"] or "")


# ── E2-4: 화면 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("needle,why", [
    ('id="preflight"', "점검 배너 자리가 없다"),
    ("+'/preflight'", "점검을 부르지 않는다"),
    ("PF_BLOCKED", "막힌 상태를 기억하지 않아 제출이 그냥 나간다"),
    ("제출할 수 없습니다", "무엇이 막았는지 화면에 안 뜬다"),
    ("이번에 결정하지 않은 후보", "'거부/미서명' 을 한 숫자로 뭉개고 있다"),
])
def test_signoff_screen_consumes_preflight(needle, why):
    from koipa.golden_review_html import render_signoff_html
    html = render_signoff_html([{"doc_id": "d", "label": "S2", "text": "x" * 80}],
                               job_id="J", post_url="/api/v1/golden/jobs/J/signoff")
    assert needle in html, why


def test_preflight_failure_does_not_block_review():
    """점검이 안 되는 것으로 검수 자체를 막지 않는다 — 조용히 넘긴다."""
    from koipa.golden_review_html import render_signoff_html
    html = render_signoff_html([{"doc_id": "d", "label": "S2", "text": "x" * 80}],
                               job_id="J", post_url="/p")
    seg = html[html.index("async function loadPreflight"):html.index("document.getElementById('submit').addEventListener")]
    assert "if(!r.ok) return;" in seg and "catch(e)" in seg

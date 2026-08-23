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
import os
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


# ── E2-6: 저장 경로 (2026-08-23) ───────────────────────────────────────────
#
# 왜 늘렸나. 실제로 제출을 막은 것이 이 점검에 **없던** 항목이었다. 223 실측: 후보 폴더
# datasets/golden_review/ff5a822c 가 호스트 계정(uid 1001) 소유라 API 컨테이너(uid 1000)가
# locked_<job>.jsonl.tmp 를 만들지 못했다. preflight 는 ok 를 냈고, 검수자는 120건을 다 고르고
# 제출한 뒤에야 KOIPA_INTERNAL 500 을 봤다 — 이 파일 첫머리에 적힌 목적이 그대로 뚫린 것이다.

def test_writability_probe_passes_on_a_normal_dir(tmp_path):
    from koipa.services.golden_build_service import _unwritable_reason
    assert _unwritable_reason(tmp_path / "locked_x.jsonl") == ""


def test_writability_probe_names_a_parent_that_is_not_a_dir(tmp_path):
    """부모 자리에 파일이 있으면 기록은 반드시 실패한다 — 사유가 비어 있으면 안 된다."""
    from koipa.services.golden_build_service import _unwritable_reason
    blocker = tmp_path / "notadir"
    blocker.write_text("x", encoding="utf-8")
    reason = _unwritable_reason(blocker / "locked_x.jsonl")
    assert reason and "notadir" in reason


def test_write_failure_becomes_a_named_storage_error(tmp_path):
    """OSError 그대로 올리면 전역 핸들러가 사유를 지운다(J2) — 화면엔 500 만 남는다."""
    import pytest as _pytest
    from koipa.services.golden_build_service import (
        GoldenSignoffStorageError,
        _atomic_write_jsonl,
    )
    blocker = tmp_path / "notadir"
    blocker.write_text("x", encoding="utf-8")
    with _pytest.raises(GoldenSignoffStorageError) as e:
        _atomic_write_jsonl(blocker / "locked_x.jsonl", [{"doc_id": "a"}])
    assert "저장하지 못했습니다" in str(e.value)


def test_preflight_blocks_when_the_signoff_store_is_unwritable(tmp_path, monkeypatch):
    """쓸 수 없으면 blocking — 경고가 아니다. 제출하면 확실히 실패하는 조건이다."""
    import koipa.services.golden_build_service as mod
    job_id = _job(tmp_path)
    monkeypatch.setattr(mod, "_unwritable_reason", lambda p: "쓰기 권한이 없습니다: /x")
    out = mod.GoldenBuildService().signoff_preflight(job_id, reviewer_id="지재원관리자")
    assert out["ok"] is False
    codes = [b["code"] for b in out["blocking"]]
    assert "signoff_store_unwritable" in codes
    assert codes.count("signoff_store_unwritable") == 1   # 같은 폴더 사유를 두 번 세지 않는다


def test_preflight_blocks_an_unwritable_publish_path(tmp_path, monkeypatch):
    import koipa.services.golden_build_service as mod
    from koipa.config import settings
    job_id = _job(tmp_path)
    live = tmp_path / "notadir"
    live.write_text("x", encoding="utf-8")
    monkeypatch.setattr(settings, "locked_eval_jsonl", str(live / "locked_gold_eval.jsonl"))
    out = mod.GoldenBuildService().signoff_preflight(
        job_id, reviewer_id="지재원관리자", publish=True
    )
    assert "publish_path_unwritable" in [b["code"] for b in out["blocking"]]
    # publish 를 안 걸면 라이브 경로는 볼 이유가 없다 — 근거 없이 버튼을 잠그지 않는다.
    out2 = mod.GoldenBuildService().signoff_preflight(job_id, reviewer_id="지재원관리자")
    assert "publish_path_unwritable" not in [b["code"] for b in out2["blocking"]]


@pytest.mark.skipif(os.name == "nt", reason="POSIX 권한 비트가 있어야 재현된다")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root 는 권한 비트를 무시한다 — 223 의 uid 1000 상황이 아니다")
def test_real_permission_failure_is_caught_before_submit(tmp_path):
    """223 에서 난 실패 그대로: 폴더에 쓰기 비트가 없다 → preflight 가 먼저 잡는다."""
    from koipa.services.golden_build_service import GoldenBuildService
    job_id = _job(tmp_path)
    svc = GoldenBuildService()
    gold_dir = Path(svc.jobs.get(job_id)["gold_path"]).parent
    mode = gold_dir.stat().st_mode
    gold_dir.chmod(0o555)
    try:
        out = svc.signoff_preflight(job_id, reviewer_id="지재원관리자")
        assert "signoff_store_unwritable" in [b["code"] for b in out["blocking"]]
    finally:
        gold_dir.chmod(mode)

"""golden_signoff.promote_to_locked — 사람 이중서명 승격 게이트(P3 골격) 계약 테스트.

고등급(TS/S1)=≥2 독립 reviewer, S2/S3=단일. 머신/불일치/미서명 거부. 승격분 tier=locked.
"""
from lloydk.golden_signoff import Signoff, promote_to_locked
from lloydk.golden_tiers import TIER_LOCKED, tier_of


def _cand(doc_id, grade):
    return {"doc_id": doc_id, "text": f"{doc_id} 본문", "label": grade,
            "label_source": "rule_llm_agreement", "review_status": "gold_candidate"}


def test_single_human_signoff_promotes_s2():
    res = promote_to_locked([_cand("d1", "S2")], [Signoff("d1", "admin_kim", "S2", "2026-09-10")])
    assert res.stats["locked"] == 1
    rec = res.locked[0]
    assert rec["label"] == "S2" and rec["label_source"] == "human_review"
    assert rec["review_status"] == "accepted" and rec["signed_at"] == "2026-09-10"
    assert tier_of(rec) == TIER_LOCKED          # golden_tiers와 정합


def test_upper_grade_needs_two_reviewers():
    # TS 단일 서명 → 부족(이중서명 필요).
    res = promote_to_locked([_cand("d1", "TS")], [Signoff("d1", "admin_kim", "TS")])
    assert res.stats["locked"] == 0 and res.stats["rejected"] == 1
    assert res.rejected[0]["reason"].startswith("insufficient_reviewers")


def test_upper_grade_dual_signoff_promotes():
    res = promote_to_locked(
        [_cand("d1", "TS")],
        [Signoff("d1", "admin_kim", "TS", "2026-09-10"),
         Signoff("d1", "admin_lee", "TS", "2026-09-11")],
    )
    assert res.stats["locked"] == 1
    rec = res.locked[0]
    assert set(rec["reviewer_ids"]) == {"admin_kim", "admin_lee"}
    assert rec["signed_at"] == "2026-09-11"     # 최신 서명시각


def test_reviewers_must_be_distinct():
    # 같은 사람 두 번 서명 → 독립 2인 아님 → 부족.
    res = promote_to_locked(
        [_cand("d1", "S1")],
        [Signoff("d1", "admin_kim", "S1"), Signoff("d1", "admin_kim", "S1")],
    )
    assert res.stats["locked"] == 0
    assert res.rejected[0]["reason"].startswith("insufficient_reviewers")


def test_grade_disagreement_rejected():
    res = promote_to_locked(
        [_cand("d1", "TS")],
        [Signoff("d1", "admin_kim", "TS"), Signoff("d1", "admin_lee", "S1")],
    )
    assert res.stats["locked"] == 0
    assert res.rejected[0]["reason"] == "grade_disagree"


def test_machine_reviewer_rejected():
    for rid in ("ai_assist", "llm_judge_local_openai", "codex", ""):
        res = promote_to_locked([_cand("d1", "S2")], [Signoff("d1", rid, "S2")])
        assert res.stats["locked"] == 0
        assert res.rejected[0]["reason"] in ("machine_reviewer", "no_signoff")


def test_no_signoff_rejected():
    res = promote_to_locked([_cand("d1", "S2")], [])
    assert res.stats["locked"] == 0 and res.rejected[0]["reason"] == "no_signoff"


def test_dual_disabled_allows_single_upper():
    res = promote_to_locked(
        [_cand("d1", "TS")], [Signoff("d1", "admin_kim", "TS")], dual_for_upper=False,
    )
    assert res.stats["locked"] == 1


def test_stats_breakdown():
    cands = [_cand("d1", "S2"), _cand("d2", "TS"), _cand("d3", "S3")]
    signs = [
        Signoff("d1", "admin_kim", "S2"),                 # S2 single → locked
        Signoff("d2", "admin_kim", "TS"),                 # TS single → reject(insufficient)
        # d3 무서명 → reject(no_signoff)
    ]
    res = promote_to_locked(cands, signs)
    assert res.stats["locked"] == 1 and res.stats["rejected"] == 2
    assert res.stats["locked_by_grade"] == {"S2": 1}
    assert res.stats["rejected_reasons"].get("no_signoff") == 1

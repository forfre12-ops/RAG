"""consensus.evaluate_consensus P1 v2 게이트 계약 테스트.

재설계: conf 제거, 게이트 = 합의(rule==llm) + 실제 근거 span + self-consistency.
gold_candidate(자동 후보)만 만들고 평가정답은 사람(P3). 구 path A/B/C·conf 게이트는 삭제됨.
"""
from koipa.modules.m3_labeling.consensus import evaluate_consensus


def test_gold_candidate_agreement_with_evidence():
    r = evaluate_consensus("S1", "S1", has_real_evidence=True, self_consistency=1.0)
    assert r.is_gold
    assert r.status == "gold_candidate"
    assert r.review_status == "gold_candidate"      # 구 'accepted' 아님
    assert r.label_source == "rule_llm_agreement"


def test_agree_but_no_evidence_not_gold():
    # 구 path B/C 사살: 룰 근거 없는 단일 LLM 동의는 더 이상 gold 아님.
    r = evaluate_consensus("S2", "S2", has_real_evidence=False, self_consistency=1.0)
    assert not r.is_gold
    assert r.status == "needs_review_no_evidence"
    assert "no_evidence" in r.flags


def test_disagree_not_gold():
    r = evaluate_consensus("S2", "S1", has_real_evidence=True, self_consistency=1.0)
    assert not r.is_gold
    assert r.status == "needs_review_disagree"


def test_conf_irrelevant_to_admission():
    # conf는 admission에 무관 — 동일 입력이면 sort_conf와 무관하게 둘 다 gold. priority만 다름.
    lo = evaluate_consensus("S2", "S2", has_real_evidence=True, self_consistency=1.0, sort_conf=0.0)
    hi = evaluate_consensus("S2", "S2", has_real_evidence=True, self_consistency=1.0, sort_conf=0.99)
    assert lo.is_gold and hi.is_gold
    assert lo.review_priority != hi.review_priority


def test_low_self_consistency_routes_review():
    r = evaluate_consensus("S1", "S1", has_real_evidence=True, self_consistency=0.5)
    assert not r.is_gold
    assert r.status == "needs_review_low_agreement"
    assert "low_agreement" in r.flags


def test_ts_downgrade_suspect():
    # 룰 TS인데 LLM이 S2로 → 미탐 의심. generic disagree보다 우선해서 surfacing.
    r = evaluate_consensus("TS", "S2", has_real_evidence=True, self_consistency=1.0)
    assert not r.is_gold
    assert r.status == "needs_review_ts_downgrade"
    assert "ts_downgrade_suspect" in r.flags


def test_shadow_disagreement_routes_review():
    # agree+근거+만장일치라도 섀도(배포 Qwen) 불일치 → production_suspect → 사람큐.
    r = evaluate_consensus("S2", "S2", has_real_evidence=True, self_consistency=1.0, shadow_grade="S3")
    assert not r.is_gold
    assert r.status == "needs_review_production_suspect"
    assert "production_suspect" in r.flags
    assert r.review_priority >= 5.0


def test_airgap_never_auto_admits_upper():
    # airgap(상용 교차검증 없음)에서 상위등급(TS/S1)은 자동확정 금지.
    r = evaluate_consensus("S1", "S1", has_real_evidence=True, self_consistency=1.0, airgap=True)
    assert not r.is_gold
    assert r.status == "needs_review_airgap_upper"
    assert "airgap_upper" in r.flags


def test_airgap_allows_lower_grades():
    # airgap이어도 비-상위등급(S2/S3) 합의+근거는 통과.
    r = evaluate_consensus("S2", "S2", has_real_evidence=True, self_consistency=1.0, airgap=True)
    assert r.is_gold and r.status == "gold_candidate"


def test_require_evidence_false_admits_agree_without_evidence():
    # 합성 빌드 모드(require_evidence=False): 룰 시드 근거 없어도 rule==llm 합의면 gold.
    # leakage-free 합성이 no_evidence로 무더기 탈락하던 문제 완화(파일럿 레버 3).
    r = evaluate_consensus("S1", "S1", has_real_evidence=False, self_consistency=1.0,
                           require_evidence=False)
    assert r.is_gold and r.status == "gold_candidate"
    # 완화는 evidence 요구뿐 — 불일치·ts_downgrade·low_agreement는 여전히 needs_review.
    r2 = evaluate_consensus("S2", "S1", has_real_evidence=False, self_consistency=1.0,
                            require_evidence=False)
    assert not r2.is_gold and r2.status == "needs_review_disagree"
    # 기본(require_evidence=True)은 운영 게이트 그대로 — 근거 없으면 no_evidence.
    r3 = evaluate_consensus("S1", "S1", has_real_evidence=False, self_consistency=1.0)
    assert not r3.is_gold and r3.status == "needs_review_no_evidence"


def test_legacy_keyword_args_swallowed():
    # 구 keyword 인자(min_rule_conf/min_llm_conf)는 **_legacy로 흡수 — TypeError 안 남(1릴리스 한시).
    r = evaluate_consensus("S2", "S2", has_real_evidence=True, min_rule_conf=0.5, min_llm_conf=0.7)
    assert r.is_gold

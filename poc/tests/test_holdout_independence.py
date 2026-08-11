"""홀드아웃 계보 독립성 + 누출 계량 — 비교를 하기 전에 세는 것.

실측(2026-08-12)이 이 모듈을 만든 이유다: v6 학습셋과 봉인 평가셋 v2_2 는 **계보상 독립**
이지만(생성기·가족·본문 겹침 0), 평가셋 자체가 길이로 답을 알려준다
(final_800 길이-only 0.960 · Theil's U 0.794 · tell 커버 1.000).
독립성만 보고 "공정 비교"라고 부르면 정확히 이 상태를 놓친다.
"""
from __future__ import annotations

from lloydk.dataset_leakage import theils_u
from lloydk.holdout_independence import assess


def _doc(doc_id, grade, text, *, family="fam-a", method="gen-v1", scenario="sc-a"):
    return {
        "doc_id": doc_id,
        "label": grade,
        "text": text,
        "document_family_id": family,
        "scenario_id": scenario,
        "authoring_method": method,
    }


def _clean_set(prefix, family, method, scenario):
    """길이가 등급을 알려주지 않는 최소 셋 — 등급마다 같은 길이 분포."""
    rows = []
    for index in range(12):
        for grade in ("TS", "S1", "S2", "S3"):
            # 접두사를 본문에도 넣는다 — 안 넣으면 train/holdout 본문이 글자 그대로 같아진다.
            body = f"{prefix} {grade} 문서 본문 {index} " + "가나다라마바사아자차 " * (5 + index)
            rows.append(
                _doc(f"{prefix}-{grade}-{index}", grade, body,
                     family=f"{family}-{index}", method=method, scenario=f"{scenario}-{index}")
            )
    return rows


def test_independent_sets_are_reported_independent():
    train = _clean_set("tr", "trf", "gen-train", "trs")
    holdout = _clean_set("ho", "hof", "gen-holdout", "hos")
    report = assess(train, holdout)
    assert report["lineage_independent"] is True


def test_shared_generator_breaks_independence_even_without_shared_text():
    """실무에서 가장 놓치기 쉬운 축 — 본문도 가족도 다른데 생성기만 같은 경우.

    같은 작문 습관을 공유하면 모델은 그 습관을 배우고, 습관이 다른 실문서에서 무너진다.
    """
    train = _clean_set("tr", "trf", "same-generator", "trs")
    holdout = _clean_set("ho", "hof", "same-generator", "hos")
    report = assess(train, holdout)
    assert report["lineage_independent"] is False
    assert any("authoring_method" in c for c in report["concerns"])
    assert report["overlap"]["document_text"]["shared"] == 0  # 본문은 안 겹친다


def test_shared_scenario_breaks_independence():
    train = _clean_set("tr", "trf", "gen-train", "shared-sc")
    holdout = _clean_set("ho", "hof", "gen-holdout", "shared-sc")
    report = assess(train, holdout)
    assert report["lineage_independent"] is False
    assert any("scenario_id" in c for c in report["concerns"])


def test_duplicate_text_is_caught_regardless_of_ids():
    train = _clean_set("tr", "trf", "gen-train", "trs")
    holdout = [dict(row, doc_id="ho-1", document_family_id="hof",
                    authoring_method="gen-holdout", scenario_id="hos")
               for row in train[:3]]
    report = assess(train, holdout)
    assert report["overlap"]["document_text"]["shared"] == 3
    assert report["lineage_independent"] is False


def test_leaky_holdout_is_unusable_even_when_independent():
    """핵심 — 독립이어도 홀드아웃이 답을 알려주면 비교에 쓸 수 없다.

    등급마다 길이 구간을 갈라 두면 본문을 안 읽어도 등급이 나온다.
    """
    train = _clean_set("tr", "trf", "gen-train", "trs")
    holdout = []
    for index in range(12):
        for size, grade in ((3, "TS"), (9, "S1"), (18, "S2"), (30, "S3")):
            holdout.append(
                _doc(f"ho-{grade}-{index}", grade, "본문 " + "가나다라마바사아자차 " * size,
                     family=f"hof-{index}", method="gen-holdout", scenario=f"hos-{index}")
            )
    report = assess(train, holdout)
    assert report["lineage_independent"] is True
    assert report["usable_for_comparison"] is False
    assert any("길이-only" in c or "Theil" in c for c in report["concerns"])


def test_claim_ceiling_is_always_present():
    """지표만 인용되고 한정이 떨어져 나가는 일이 반복됐다 — 보고서에 박아 둔다."""
    report = assess(_clean_set("tr", "trf", "g1", "s1"), _clean_set("ho", "hof", "g2", "s2"))
    assert "실문서 일반화 근거가 아니다" in report["claim_ceiling"]
    assert report["usable_for_comparison"] is True  # 통과해도 천장은 그대로다


# ── Theil's U ──────────────────────────────────────────────────────────────
def test_theils_u_is_zero_when_length_says_nothing():
    pairs = [(100, g) for g in ("TS", "S1", "S2", "S3")] * 25
    assert theils_u(pairs) < 0.05


def test_theils_u_is_near_one_when_length_decides_the_grade():
    """분위수 버킷을 쓰므로 완전 결정이어도 1.0 이 아니다.

    4등급 × 25건을 10버킷에 나누면 등급 경계마다 한 버킷이 두 등급을 섞는다(실측 0.90).
    절대 구간 대신 분위수를 쓰는 이유는 코퍼스마다 길이 스케일이 달라서다 — 그 대가로
    상한이 살짝 깎인다. 권고 상한 0.25 와는 거리가 멀어 판단에 영향이 없다.
    """
    pairs = []
    for index in range(25):
        for offset, grade in ((0, "TS"), (1000, "S1"), (2000, "S2"), (3000, "S3")):
            pairs.append((offset + index, grade))
    assert theils_u(pairs) > 0.85


def test_theils_u_handles_degenerate_input():
    assert theils_u([]) == 0.0
    assert theils_u([(10, "TS")]) == 0.0
    assert theils_u([(10, "TS"), (20, "TS")]) == 0.0  # 등급이 하나면 잴 것이 없다

"""홀드아웃 계보 독립성 + 누출 계량 — 비교를 하기 전에 세는 것.

실측(2026-08-12)이 이 모듈을 만든 이유다: v6 학습셋과 봉인 평가셋 v2_2 는 **계보상 독립**
이지만(생성기·가족·본문 겹침 0), 평가셋 자체가 길이로 답을 알려준다
(final_800 길이-only 0.960 · Theil's U 0.794 · tell 커버 1.000).
독립성만 보고 "공정 비교"라고 부르면 정확히 이 상태를 놓친다.
"""
from __future__ import annotations

from koipa.dataset_leakage import theils_u
from koipa.holdout_independence import assess


def _doc(doc_id, grade, text, *, family="fam-a", method="gen-v1", scenario="sc-a"):
    return {
        "doc_id": doc_id,
        "label": grade,
        "text": text,
        "document_family_id": family,
        "scenario_id": scenario,
        "authoring_method": method,
    }


def _clean_set(prefix, family, method, scenario, *, count=25):
    """누출이 없는 최소 셋 — 본문이 등급을 말하지 않고 길이도 등급과 무관하다.

    두 가지를 지켜야 픽스처가 스스로 누출을 만들지 않는다:
      · 본문에 등급 문자열을 넣지 않는다(넣으면 그 문장이 곧 tell 이 된다)
      · 문장 종결부(".")를 둔다 — 누출 지표는 문장 단위로 세므로 종결부가 없으면
        문서 전체가 문장 하나로 잡혀 검사가 의미를 잃는다(실문서에는 늘 있다)
    접두사는 본문에도 넣는다 — 안 넣으면 train/holdout 본문이 글자 그대로 같아진다.
    """
    rows = []
    for index in range(count):
        for grade in ("TS", "S1", "S2", "S3"):
            body = " ".join(
                f"{prefix} 계열 검토 기록 {index}-{n} 항목을 대조해 남긴다."
                for n in range(5 + index)
            )
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
    # index 가 같으면 등급이 달라도 본문이 같으므로(픽스처가 등급을 본문에 안 쓴다)
    # 서로 다른 index 에서 골라야 본문 3종이 된다.
    picked = [train[0], train[4], train[8]]
    holdout = [dict(row, doc_id=f"ho-{n}", document_family_id="hof",
                    authoring_method="gen-holdout", scenario_id="hos")
               for n, row in enumerate(picked)]
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
            body = " ".join(
                f"홀드아웃 {grade} 계열 기록 {index}-{n} 항목을 확인해 남긴다."
                for n in range(size)
            )
            holdout.append(
                _doc(f"ho-{grade}-{index}", grade, body,
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


# ── 문장 공유 축 (2026-08-12 추가) ─────────────────────────────────────────
# 이 축은 초판에 없었다. 초판은 메타데이터 세 축(본문 해시·가족·생성기)만 봤고, 그래서
# **메타데이터를 다르게 붙이고 같은 문장 풀에서 본문을 뽑으면 "독립"으로 통과**했다.
# v6 문장 풀을 평가셋에도 쓰려던 참에 드러난 구멍이라 그대로 고정한다.

def _from_shared_pool(prefix, family, method, scenario, pool):
    rows = []
    for index in range(12):
        for grade in ("TS", "S1", "S2", "S3"):
            filler = " ".join(
                f"{prefix} 계열 부가 기록 {index}-{n} 항목을 정리한다."
                for n in range(4 + index)
            )
            body = f"{pool[index % len(pool)]} {filler}"
            rows.append(
                _doc(f"{prefix}-{grade}-{index}", grade, body,
                     family=f"{family}-{index}", method=method, scenario=f"{scenario}-{index}")
            )
    return rows


_POOL = (
    "이 문서가 인용한 기준은 공개 규격과 공개 안내자료에서 그대로 확인된다.",
    "열람 범위를 지정 담당자로 제한하고 반출은 승인 기록을 남긴 뒤 진행한다.",
    "공개 자료만으로는 같은 결과를 다시 만들 수 없는 조건이 함께 적혀 있다.",
)


def test_shared_sentence_pool_breaks_independence_despite_clean_metadata():
    """핵심 — 메타데이터 세 축은 전부 깨끗한데 문장 풀만 같은 경우."""
    train = _from_shared_pool("tr", "trf", "gen-train", "trs", _POOL)
    holdout = _from_shared_pool("ho", "hof", "gen-holdout", "hos", _POOL)
    report = assess(train, holdout)

    # 메타데이터 축은 전부 통과한다 — 그래서 초판이 이걸 놓쳤다.
    assert report["overlap"]["document_text"]["shared"] == 0
    assert all(v["shared"] == 0 for v in report["overlap"]["family"].values())
    assert all(v["shared"] == 0 for v in report["overlap"]["generator"].values())

    # 문장 축이 잡는다.
    assert report["overlap"]["shared_sentences"]["coverage"] == 1.0
    assert report["lineage_independent"] is False
    assert any("문장을 품고 있다" in c for c in report["concerns"])


def test_separate_pools_stay_independent():
    """평가셋을 **별도 문장 풀**로 만들면 통과해야 한다 — 이게 권고하는 방식이다."""
    other = (
        "본 자료의 근거는 배포된 표준 문서에서 항목 단위로 대조된다.",
        "접근 권한은 직무 단위로 부여하고 분기마다 목록을 재확인한다.",
        "외부 공표 자료로는 이 조합의 적용 순서를 확인할 수 없다.",
    )
    train = _from_shared_pool("tr", "trf", "gen-train", "trs", _POOL)
    holdout = _from_shared_pool("ho", "hof", "gen-holdout", "hos", other)
    report = assess(train, holdout)
    assert report["overlap"]["shared_sentences"]["coverage"] == 0.0
    assert report["lineage_independent"] is True


def test_incidental_boilerplate_overlap_does_not_trip_the_axis():
    """상투어 한두 종이 우연히 겹치는 것까지 막으면 경보가 무뎌진다."""
    train = _clean_set("tr", "trf", "gen-train", "trs")
    holdout = _clean_set("ho", "hof", "gen-holdout", "hos")
    shared = "검토 결과와 후속 조치는 담당자와 기한을 함께 적어 남긴다."
    holdout[0]["text"] += " " + shared          # 100건 중 1건만 = 1%
    train[0]["text"] += " " + shared
    report = assess(train, holdout)
    assert 0 < report["overlap"]["shared_sentences"]["coverage"] <= 0.02
    assert report["lineage_independent"] is True

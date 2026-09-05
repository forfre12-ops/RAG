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


# ── 문장 공유의 갈래 보고 (2026-09-05) ──────────────────────────────────────
#
# 커버리지 하나로는 "왜 걸렸는지"를 못 판다. 길이를 균형 잡아 Theil's U 0.074 ·
# 길이-only 0.242(무작위 0.25 보다 낮다)까지 내린 홀드아웃에서도 커버리지가 0.124 였고,
# 공유 20종을 전부 읽어 보니 **같은 원본은 하나도 없었다** — 판결문 서식 6 · 생성기
# 템플릿 9 · 보고서 서식 1. 그래서 상투어를 뺀 값을 함께 낸다.
#
# ⚠ 이 갈래는 선별 보조이지 판정이 아니다. 판정은 바뀌지 않는다는 것도 함께 고정한다.

_BOILER = "그러므로 상고를 기각하고 상고비용은 패소자의 부담으로 하기로 하여 주문과 같이 판결한다."
_UNIQUE = "이 사건 고안은 말굽형 영구자석의 양단에 막대자석의 반대극을 배치한 자화수 제조장치이다."


def _row(text, label="S3"):
    return {"text": text, "label": label}


def _filler(i):
    return "검토 결과와 산정 근거를 정리한 %s 항목의 내용이다." % chr(ord("가") + i)


def test_boilerplate_and_distinctive_are_reported_separately():
    """여러 학습 문서에 나오는 문장은 상투어로 따로 센다."""
    train = [_row(" ".join([_BOILER, _filler(i), _filler(i + 20)])) for i in range(5)]
    holdout = [_row(" ".join([_BOILER, _filler(50 + i), _filler(60 + i)])) for i in range(4)]
    rep = assess(train, holdout)
    ss = rep["overlap"]["shared_sentences"]
    assert ss["coverage"] == 1.0, ss              # 전 문서가 상투어를 공유한다
    assert ss["boilerplate_types"] >= 1
    assert ss["distinctive_coverage"] == 0.0, ss  # 상투어를 빼면 0


def test_distinctive_sharing_is_still_counted():
    """한 문서에만 있는 문장을 공유하면 상투어를 빼도 남는다 — 놓치지 않는다."""
    train = [_row(" ".join([_UNIQUE, _filler(0), _filler(1)]))]
    holdout = [_row(" ".join([_UNIQUE, _filler(70), _filler(71)]))]
    ss = assess(train, holdout)["overlap"]["shared_sentences"]
    assert ss["distinctive_coverage"] == 1.0, ss
    assert ss["distinctive_types"] >= 1


def test_verdict_is_unchanged_by_the_breakdown():
    """갈래를 더해도 판정은 그대로 — 도구가 스스로 느슨해지면 안 된다."""
    train = [_row(" ".join([_BOILER, _filler(i)])) for i in range(5)]
    holdout = [_row(" ".join([_BOILER, _filler(80 + i)])) for i in range(4)]
    rep = assess(train, holdout)
    ss = rep["overlap"]["shared_sentences"]
    assert ss["distinctive_coverage"] == 0.0
    # 상투어만 겹쳐도 커버리지가 문턱을 넘으면 여전히 '독립 아님'이다.
    assert rep["lineage_independent"] is False
    assert any("같은 문장을 품고" in c for c in rep["concerns"])


def test_concern_text_carries_the_breakdown():
    """지표만 인용되고 한정이 떨어져 나가지 않도록 문구에 갈래를 넣는다."""
    train = [_row(" ".join([_BOILER, _filler(i)])) for i in range(5)]
    holdout = [_row(" ".join([_BOILER, _filler(90 + i)])) for i in range(4)]
    msg = [c for c in assess(train, holdout)["concerns"] if "같은 문장" in c][0]
    assert "상투어" in msg and "빼면" in msg, msg


# ── 한 등급뿐인 평가셋 (2026-09-05) ─────────────────────────────────────────
#
# 과분류 측정 전용 셋은 전부 같은 등급이다(공개문서 300건 = 전부 S3). 그런 셋에서 길이
# 1-NN 은 이웃이 무조건 같은 등급이라 **항상 1.000** 이 나오고 무작위 기대값도 1.000 이다.
# 그런데 경고는 무작위 기준선을 보지 않고 1nn > 0.55 만 봐서 늘 켜졌고, 학습셋과 본문중복
# 0 · 문장공유 0.0000 인 완전 독립 셋이 usable_for_comparison=false 로 막혔다.
#
# 설명을 concerns 에 넣으면 안 된다 — usable = independent and not concerns 라 설명이 곧
# 차단이 된다. notes 로 갈랐다.


def _single(n=12):
    return [_row("공개 안내문 %d 호로 배포한 자료이며 열람 제한이 없다. " % i * (3 + i % 5))
            for i in range(n)]


def test_single_grade_set_is_not_blocked_by_length():
    train = [_row(" ".join(_filler(i) for i in range(4)), label="S2") for i in range(20)]
    rep = assess(train, _single())
    assert rep["overlap"]["shared_sentences"]["coverage"] == 0.0
    assert rep["concerns"] == [], rep["concerns"]
    assert rep["usable_for_comparison"] is True


def test_single_grade_set_says_why_length_was_skipped():
    """조용히 빼지 않는다 — 왜 안 봤는지가 남는다."""
    train = [_row(" ".join(_filler(i) for i in range(4)), label="S2") for i in range(20)]
    notes = assess(train, _single())["notes"]
    assert notes and "등급이 1종뿐" in notes[0]
    # 값 자체는 그대로 보고한다(숨기지 않는다).
    assert assess(train, _single())["holdout_leakage"]["length_only_1nn"] == 1.0


def test_multi_grade_set_still_judged_on_length():
    """등급이 둘 이상이면 길이 축은 그대로 판정에 쓴다(회귀 방지)."""
    train = [_row(" ".join(_filler(i) for i in range(4)), label="S2") for i in range(20)]
    skewed = ([_row("짧은 안내 문장이 반복되는 공개 자료이다. " * 2, label="S3") for _ in range(8)]
              + [_row("긴 내부 검토 자료의 본문이 이어진다. " * 40, label="TS") for _ in range(8)])
    rep = assess(train, skewed)
    assert rep["notes"] == []
    assert any("길이" in c or "Theil" in c for c in rep["concerns"]), rep["concerns"]


def test_notes_never_block_the_verdict():
    """notes 는 판정을 바꾸지 않는다 — concerns 와 갈라 둔 이유."""
    train = [_row(" ".join(_filler(i) for i in range(4)), label="S2") for i in range(20)]
    rep = assess(train, _single())
    assert rep["notes"], "이 셋은 note 가 있어야 한다"
    assert rep["usable_for_comparison"] is True


# ── 오염은 정형 문구가 아니라 '같은 원본' 이다 (2026-09-05) ──────────────────
#
# 임계 0.02 의 근거는 "상투어는 한두 종"이라는 전제였다. **한국어 판결문은 정형 문구가
# 수십 종**이라(맺음말·인용 서식이 25자를 넘어 정규화를 통과한다) 판례가 든 홀드아웃은
# 어떻게 만들어도 그 문턱을 못 넘었다.
#
#     셋            문장공유   같은원본   판정 근거
#     hardened42     0.0952     1건     길이축도 실패 → 막힘
#     v5 test        0.1875     4건     길이축도 실패 → 막힘
#     길이균형        0.1200     0건     오염 0 · 길이축 통과인데 **이것 하나로 막혔다**
#
# 길이균형 셋의 공유 19종을 전수 읽었다 — **같은 원본은 0건**, 전부 정형이었다.
#
# 그래서 판정에 same_source_documents 를 넣고(clean_holdout_leakage 와 같은 검증된 기준),
# 커버리지는 **문장 풀 공유**를 잡는 자리로 남겼다(모듈 주석이 스스로 "풀 공유는 1.0
# 근처"라고 적었다).
#
# ⚠ 느슨하게 푼 것이 아님을 시험으로 고정한다 — 같은 원본이 있으면 여전히 막힌다.

_LEGAL_BOILER = [
    "그러므로 상고를 기각하고 상고비용은 패소자의 부담으로 하기로 하여 주문과 같이 판결한다.",
    "선고 #후# 판결(공#상, #), 대법원 #. 자 #항원# 심결 주문 상고를 기각한다.",
    "이에 원심결을 파기하고 사건을 특허청 항고심판소에 환송하기로 의견이 일치되었다.",
]


def _case(i, n_body=6):
    """사건마다 고유한 본문 + 공통 맺음말."""
    body = [_filler(i * 20 + k) for k in range(n_body)]
    return _row(" ".join(body + _LEGAL_BOILER))


def test_boilerplate_only_still_blocks_and_that_is_the_open_problem():
    """정형 문구만 공유해도 **여전히 막힌다** — 아직 풀지 못한 문제라 시험으로 남긴다.

    ⚠ 2026-09-05 에 이것을 통과시키려고 판정을 "상투어를 뺀 커버리지"로 옮겨 봤다가
      **되돌렸다.** 그렇게 하면 문장 풀 공유 보호가 깨진다 — 풀 문장이 학습셋 여러 문서에
      나오면 빈도 기준이 그것을 상투어로 분류해 버린다(바로 아래 풀 공유 시험이 잡았다).

      즉 **빈도로는 정형 문구와 공유 풀을 못 가른다.** 다른 기준이 필요하고 그것은
      사람 판단이다. 이 시험은 "고쳐야 할 것"이 아니라 **현재 상태의 기록**이다 —
      기준이 정해져 통과하게 되면 그때 이 시험을 뒤집는다.

    실측 영향: 길이 균형 홀드아웃(같은 원본 0 · Theil's U 0.080 · 공유 20종 전수 확인
    결과 오염 0)이 이것 하나 때문에 usable_for_comparison=false 로 남는다.
    """
    train = [_case(i) for i in range(8)]
    holdout = [_case(50 + i) for i in range(6)]      # 본문은 전부 다르고 맺음말만 공유
    rep = assess(train, holdout)
    assert rep["overlap"]["same_source_documents"]["documents"] == 0, "오염은 없다"
    assert rep["overlap"]["shared_sentences"]["coverage"] > 0.02, "상투어는 실제로 겹친다"
    assert rep["lineage_independent"] is False, "그런데도 막힌다 — 이것이 남은 문제다"


def test_same_source_document_still_blocks():
    """같은 원본이 길이만 달리해 들어오면 여전히 막는다 — 느슨해지지 않았다."""
    full = [_filler(i) for i in range(12)]
    train = [_row(" ".join(full))] + [_case(i) for i in range(5)]
    truncated = _row(" ".join(full[:9]))              # 같은 문서의 절단본
    rep = assess(train, [truncated] + [_case(60 + i) for i in range(5)])
    assert rep["overlap"]["same_source_documents"]["documents"] == 1
    assert rep["lineage_independent"] is False
    assert any("같은 원본" in c for c in rep["concerns"]), rep["concerns"]


def test_shared_sentence_pool_still_blocks():
    """문장 풀을 통째로 공유하면 여전히 막는다 — 커버리지 축이 지키는 자리."""
    pool = [_filler(i) for i in range(10)]
    train = [_row(" ".join(pool[:6])), _row(" ".join(pool[3:9])), _row(" ".join(pool[2:8]))]
    # 홀드아웃 전 문서가 같은 풀에서 나온다 → 커버리지가 1.0 로 간다
    holdout = [_row(" ".join(pool[1:7])), _row(" ".join(pool[4:10])), _row(" ".join(pool[0:6]))]
    rep = assess(train, holdout)
    assert rep["overlap"]["shared_sentences"]["coverage"] > 0.50
    assert rep["lineage_independent"] is False


def test_same_source_report_names_the_pair():
    """어느 문서와 겹치는지 남긴다 — '몇 건'만으로는 확인할 수 없다."""
    full = [_filler(i) for i in range(12)]
    train = [{"doc_id": "TRAIN-1", "label": "S3", "text": " ".join(full)}]
    holdout = [{"doc_id": "HOLD-1", "label": "S3", "text": " ".join(full[:9])}]
    ex = assess(train, holdout)["overlap"]["same_source_documents"]["examples"][0]
    assert ex["holdout_doc_id"] == "HOLD-1" and ex["train_doc_id"] == "TRAIN-1"
    assert ex["shared_sentences"] >= 3 and ex["ratio"] >= 0.60

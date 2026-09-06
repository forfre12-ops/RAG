"""합성 누출 게이트 — 생성 직후·검수큐 적재 전에 거른다.

실제로 있었던 실패를 재현해 잠근다: 옛 산출물의 33.8%가 본문에 자기 등급명을 노출한 채
검수큐에 쌓였다(S1 은 94.3%). 검수자가 답이 적힌 문서를 읽으면 검수가 검증이 아니라 확인
절차가 된다 — 그 문서가 큐에 들어가지 않아야 한다.
"""

from __future__ import annotations

from koipa.services.synth_quality import MIN_DOCS_FOR_CORPUS_METRICS, screen_batch


def _body(grade: str, i: int, *, length: int = 400) -> str:
    """등급 단서가 없는 평범한 본문. 길이를 등급과 무관하게 맞춘다."""
    base = (
        f"본 문서는 {i}차 회의에서 논의된 사항을 정리한 것이다. "
        "담당 부서는 후속 조치를 기한 내에 수행하고 결과를 회신한다. "
        "관련 자료는 사내 절차에 따라 보관하며 변경 이력을 남긴다. "
    )
    out = base
    while len(out) < length:
        out += base
    return out[:length]


def test_grade_token_in_body_is_flagged_even_in_tiny_batch():
    """등급명 노출은 문서 한 건으로도 판정된다 — 표본 수와 무관하게 본다."""
    docs = [
        ("S1", "본 문서의 등급은 S1 으로 지정한다. " + _body("S1", 1)),
        ("S2", _body("S2", 2)),
    ]
    out = screen_batch(docs)
    assert 0 not in out["admit"], "등급명이 적힌 문서가 검수큐로 갔다"
    assert 1 in out["admit"], "멀쩡한 문서까지 막았다"
    assert {f["reason"] for f in out["flagged"]} == {"grade_token_exposed"}


def test_clean_small_batch_passes_with_explicit_verdict():
    """표본이 적으면 코퍼스 지표는 판정하지 않는다 — 거짓 양성 방지."""
    docs = [(g, _body(g, i)) for i, g in enumerate(["TS", "S1", "S2", "S3"])]
    out = screen_batch(docs)
    assert out["admit"] == [0, 1, 2, 3]
    assert out["batch_verdict"] == "too_small_for_corpus_metrics"
    assert out["flagged"] == []


def test_length_separated_corpus_is_held():
    """등급별로 길이가 갈리면 본문을 안 읽어도 등급이 맞는다 — 배치째 보류."""
    docs = []
    for i in range(MIN_DOCS_FOR_CORPUS_METRICS):
        grade = ["TS", "S1", "S2", "S3"][i % 4]
        # 등급마다 길이 밴드를 완전히 갈라 놓는다(TS 가장 길고 S3 가장 짧게).
        length = {"TS": 3000, "S1": 1600, "S2": 800, "S3": 300}[grade]
        docs.append((grade, _body(grade, i, length=length)))
    out = screen_batch(docs)
    assert out["batch_verdict"] == "corpus_leak"
    assert out["admit"] == [], "길이만으로 등급이 갈리는 배치가 통과했다"
    assert out["metrics"]["length_only_1nn"] > 0.55


def test_clean_large_batch_passes():
    """길이가 고르고 등급 단서가 없으면 통과한다 — 게이트가 다 막으면 못 쓴다.

    등급마다 **같은 길이 집합**을 준다. 길이를 알아도 등급을 좁힐 수 없어야 통과한다
    (길이를 등급과 '대충 흩는' 것으론 모자란다 — 주기가 맞으면 상관이 생겨 게이트가
    제대로 잡는다. 처음 쓴 픽스처가 실제로 그랬다: length_only_1nn 0.938).
    """
    lengths = [400, 460, 520, 580, 640, 700, 760, 820]
    docs = []
    for grade in ["TS", "S1", "S2", "S3"]:
        for j, ln in enumerate(lengths):
            docs.append((grade, _body(grade, j, length=ln)))
    out = screen_batch(docs)
    assert out["batch_verdict"] == "ok", out["metrics"]
    assert len(out["admit"]) == len(docs)
    assert out["metrics"]["grade_token_exposed"] == 0


# ── 한 등급뿐인 배치 (2026-09-06) ──────────────────────────────────────────────
#
# 약한 칸을 메우는 증강은 본디 한 등급이다 — S1 이 약하니 S1 만 만든다. 그런데 코퍼스
# 지표 둘 다 한 등급에서는 정의상 최대값이 되어 게이트가 전량을 막았다.
#
#   길이  1-NN 은 이웃이 무조건 같은 등급이라 항상 1.000. audit() 이 함께 내는 무작위
#         기대값(length_only_random)도 1.000 이다 — 정보가 0인 값으로 막고 있었다.
#   tell  "등장의 95% 이상이 한 등급" 이 등급 하나면 무조건 100%. 3건 이상 반복되는
#         상투구가 있으면 등급 정보가 없는데도 tell 로 잡힌다.
#
# 실측(datasets/ab_s1): qwen 60/60 · gemma 60/60 이 corpus_leak 으로 전량 보류됐다.
# 등급명 노출 0 · tell 문장 0 인 배치였다 — 걸릴 이유가 없었다.
#
# 같은 결함을 holdout_independence 가 갖고 있었고 9acb882e 가 길이 축을 뺐다.

def _docs(grade: str, n: int, body: str) -> list[tuple[str, str]]:
    # 문서마다 다른 꼬리를 붙인다 — 같은 문장이 반복되면 tell 축이 따로 켜진다.
    return [(grade, f"{body} 항목 {i} 에 대한 확인 결과를 기록한다." ) for i in range(n)]


def test_single_grade_batch_is_not_blocked_by_corpus_metrics():
    """한 등급 배치는 코퍼스 지표로 막지 않는다 — 그 지표가 성립하지 않는 구성이다."""
    from koipa.services.synth_quality import screen_batch

    docs = _docs("S1", 40, "공정 개선 검토 자료다. " * 30)
    result = screen_batch(docs)

    assert result["batch_verdict"] == "single_grade_corpus_metrics_skipped"
    assert len(result["admit"]) == len(docs), "한 등급이라는 이유로 문서가 막히면 안 된다"
    assert not result["flagged"]
    # 값은 그대로 보고한다 — 판정에 쓰지 않을 뿐이다.
    assert result["metrics"]["length_only_1nn"] == result["metrics"]["length_only_random"]


def test_single_grade_batch_still_blocks_grade_token():
    """등급명 노출은 문서 한 건으로 판정된다 — 한 등급이어도 그대로 막는다."""
    from koipa.services.synth_quality import screen_batch

    docs = _docs("S1", 40, "공정 개선 검토 자료다. " * 30)
    docs[3] = ("S1", "본 문서는 1급 비밀로 분류된 자료입니다. " + "본문 " * 200)
    result = screen_batch(docs)

    reasons = {f["reason"] for f in result["flagged"]}
    assert reasons == {"grade_token_exposed"}
    assert 3 not in result["admit"]
    assert len(result["admit"]) == len(docs) - 1


def test_two_grade_batch_split_by_length_is_still_blocked():
    """등급이 둘 이상이면 길이 축은 그대로 본다 — 이 완화가 게이트를 무디게 하면 안 된다."""
    from koipa.services.synth_quality import screen_batch

    short = _docs("S3", 30, "공개 안내 자료다. " * 10)
    long_ = _docs("TS", 30, "내부 검토 자료다. " * 200)
    result = screen_batch(short + long_)

    assert result["batch_verdict"] == "corpus_leak"
    assert result["admit"] == []


# ── 문서 품질 하한 (2026-09-06) ────────────────────────────────────────────
#
# 8지표 하한(한글비율·고유4gram·문단수·긴문단·수치사실·중복문단)이 proxy_corpus 에만
# 걸려 있었다 — _quality_errors 호출부가 그 파일 한 곳뿐이라, 같은 합성 문서가 어느
# 버튼으로 만들었느냐에 따라 다른 기준을 받았다.
#
# **떨어뜨리지는 않고 표시만 한다.** 이 하한은 고등급 1,200자 이상 구조 문서 기준으로
# 잡힌 값이고 콘솔 생성은 600~2,000자다. 얇다고 검수자에게서 감추면 사람이 볼 기회가
# 없어진다 — 검수는 "쓸 수 있나"를 사람이 판단하는 자리다.
#
# 실측(현행 생성기 60건): 통과 53 · 표시 7 (문단 5개 미만 5 · 수치 3개 미만 2)

def test_thin_document_is_flagged_but_not_dropped():
    from koipa.services.synth_quality import screen_batch

    thin = ("S1", "한 문단짜리 짧은 메모다. " * 12)          # 문단 1개 · 수치 0개
    result = screen_batch([thin])

    assert 0 in result["admit"], "얇다고 검수자에게서 감추면 안 된다"
    assert not result["flagged"], "품질은 '걸림'이 아니라 '표시'다"
    reasons = [q["reason"] for q in result["quality_flagged"]]
    assert reasons and reasons[0].startswith("low_quality:quality:")
    assert result["metrics"]["low_quality_documents"] == 1


def test_document_shaped_text_is_not_flagged():
    from koipa.services.synth_quality import screen_batch

    body = "\n\n".join(
        f"{i}. 검토 항목\n{i}차 점검에서 확인한 수치는 {i * 7}건이며 기준치 {i * 11} 대비"
        f" 차이는 {i * 4} 이다. 담당 역할과 기한을 함께 적는다."
        for i in range(1, 7)
    )
    result = screen_batch([("S1", body)])

    assert result["admit"] == [0]
    assert result["quality_flagged"] == []
    assert result["metrics"]["low_quality_documents"] == 0

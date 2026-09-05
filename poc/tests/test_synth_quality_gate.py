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

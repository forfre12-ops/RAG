"""v6 코퍼스 — 등급이 결론 문장이 아니라 요인 조합으로 드러나는가.

v3_9 계보의 결함(실측 2026-08-12): evidence 인용 8,100건 중 7,150건(88.3%)이 등급 전용
문장 안에 있었다. 근거가 본문 사실이 아니라 정답을 적어 둔 문장을 되가리키고 있었고,
그래서 proxy_corpus(인용이 있을 것)와 dataset_leakage(그 문장이 없을 것) 두 게이트가
동시에 만족될 수 없었다.

여기서 고정하는 것은 **문장을 등급이 아니라 요인 수준으로 고른다**는 성질이다. 산출된
jsonl 을 검사하지 않는다 — 그건 datasets/ 에 있고 정책상 추적 대상이 아니다. 변환 함수의
성질을 직접 본다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "v6_builder", _SCRIPTS / "build_direct_authored_catalog_training_corpus_v6.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v6 = _load_builder()
from v6_fact_pools import FACTOR_SCORE_KEY, POOLS  # noqa: E402


def test_every_factor_level_used_by_the_catalog_has_a_pool():
    """수준에 문장 풀이 없으면 빌드가 죽는다 — 조용히 등급 문장으로 되돌아가지 않게."""
    for factor, key in FACTOR_SCORE_KEY.items():
        for level in (0, 1, 2):
            assert POOLS[factor].get(level), f"{factor}(={key}) 수준 {level} 풀 없음"


def test_sentences_are_selected_by_factor_level_not_grade():
    """핵심 성질 — 등급이 달라도 요인 수준이 같으면 같은 문장이 나온다.

    이게 깨지면 그 문단은 정의상 등급 전용 문장이 되고, v3_9 의 결함으로 되돌아간다.
    """
    base = {"doc_id": "direct-catalog-v3_9-x-0001"}
    for factor in POOLS:
        level = 2
        picked_ts = v6._pick({**base, "label": "TS"}, factor, level)
        picked_s1 = v6._pick({**base, "label": "S1"}, factor, level)
        assert picked_ts == picked_s1, f"{factor} 문장이 등급에 따라 달라진다"


def test_selection_seed_ignores_ordinal_and_grade():
    """씨앗은 doc_id 다 — 순번을 쓰면 소스가 등급별로 묶여 있을 때 그대로 등급과 상관된다."""
    a = v6._seed({"doc_id": "d-1", "label": "TS"}, "nonpublicity")
    b = v6._seed({"doc_id": "d-1", "label": "S3"}, "nonpublicity")
    c = v6._seed({"doc_id": "d-2", "label": "TS"}, "nonpublicity")
    assert a == b and a != c


def test_s3_only_levels_have_enough_paraphrases():
    """secrecy=0·value=0 은 이 카탈로그에서 S3 에만 나온다(각 S3 문서의 30%·50%).

    진짜 판별 사실이라 남기되, 문장이 한 가지면 모델은 사실이 아니라 그 문자열을 외운다.
    tell 판정 하한이 '그 등급 문서의 5%' 이므로 각 변형이 그 아래로 내려가야 한다.
    """
    assert len(POOLS["nonpublicity"][0]) >= 12, "secrecy=0 변형 부족 — 30%/n < 5% 필요"
    assert len(POOLS["competitive_value"][0]) >= 16, "value=0 변형 부족 — 50%/n < 5% 필요"


def test_quotes_carry_no_grade_marker():
    """인용에 등급 표기가 있으면 proxy_corpus 가 evidence_grade_marker 로 거부한다."""
    from lloydk.proxy_corpus import _DIRECT_GRADE_MARKER

    for factor, levels in POOLS.items():
        for level, pool in levels.items():
            for quote, _tail in pool:
                assert not _DIRECT_GRADE_MARKER.search(quote), f"{factor}/{level}: {quote}"


def test_quote_length_fits_the_evidence_contract():
    """proxy_corpus 는 인용을 12~240자로 요구한다."""
    for factor, levels in POOLS.items():
        for level, pool in levels.items():
            for quote, _tail in pool:
                assert 12 <= len(quote) <= 240, f"{factor}/{level}: {len(quote)}자"


def test_quotes_are_unique_across_factors_and_levels():
    """같은 문자열이 두 곳에서 나오면 본문에서 span 이 모호해진다(빌더가 거부한다)."""
    seen: dict[str, tuple[str, int]] = {}
    for factor, levels in POOLS.items():
        for level, pool in levels.items():
            for quote, _tail in pool:
                assert quote not in seen, f"{quote!r} 가 {seen.get(quote)} 와 중복"
                seen[quote] = (factor, level)


def test_verdict_section_titles_are_grade_neutral():
    """제목도 등급을 말했다 — '핵심 보호 근거'=TS · '공개·저민감 판단'=S3.

    본문만 고치고 제목을 두면 모델은 제목을 외운다.
    """
    banned = ("핵심", "고위험", "중대", "제한", "민감", "저민감", "내부자료")
    for title in v6._NEUTRAL_SECTION_TITLES:
        assert not any(word in title for word in banned), title
    # 그리고 등급을 말하던 제목들이 전부 교체 대상에 들어 있어야 한다.
    for title in ("핵심 보호 근거", "공개·저민감 판단", "제한 공유 판단", "중간 민감도 검토"):
        assert title in v6._VERDICT_SECTIONS


def test_rebuild_evidence_rejects_stale_offsets():
    """v5 가 놓친 것 — 본문을 고치고 옛 오프셋을 두면 전건이 죽는다.

    새 본문에서 인용을 못 찾으면 조용히 넘어가지 않고 실패해야 한다.
    """
    with pytest.raises(v6.V6BuildError):
        v6._rebuild_evidence("본문에 없는 내용", {"nonpublicity": "여기 없는 인용"}, "d-1")


def test_rebuild_evidence_rejects_ambiguous_duplicate_quote():
    quote = "같은 문장이 두 번 나오면 span 이 모호하다"
    text = f"{quote} 그리고 또 {quote}"
    with pytest.raises(v6.V6BuildError):
        v6._rebuild_evidence(text, {"nonpublicity": quote}, "d-1")


def test_rebuild_evidence_anchors_exactly():
    quote = "이 문서가 인용한 기준은 공개 규격과 공개 안내자료에서 그대로 확인된다"
    text = f"머리말입니다.\n\n{quote}. 뒤 문장."
    card = v6._rebuild_evidence(text, {"nonpublicity": quote}, "d-1")
    span = card["factors"]["nonpublicity"]["spans"][0]
    assert text[span["start"]: span["end"]] == quote
    assert span["quote_sha256"] == v6._sha256_text(quote)

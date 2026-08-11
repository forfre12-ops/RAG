"""평가셋 v3 — 학습셋과 문장을 공유하지 않고, 요인 배정이 등급과 1:1이 아닌가.

두 가지를 고정한다. 둘 다 실제로 어긋났던 것이다:

1. **문장 풀 분리.** 초안에서 tail 한 줄을 학습셋 풀에서 그대로 복사했고
   (`대체 가능한 공개 정보로는 같은 판단에 이르지 못한다.`), 그 한 줄이 평가 문서 8.8% 에
   퍼졌다. 같은 문장을 공유하면 모델은 요인이 아니라 그 문장을 외우고, 평가셋은 암기량을
   재게 된다. 사람 눈으로는 안 보이므로 테스트로 막는다.

2. **요인 배정 혼합.** v2_2 는 9개 요인 수준 중 6개가 등급과 1:1이었다
   (management=1 ⟺ S2 250/250 · management=2 ⟺ TS 200/200 …). 그러면 그 수준을 서술한
   어떤 문장도 한 등급에만 나오므로 문장을 바꿔 써서 피할 수 없고, 무엇보다 **현실에 없는
   관계를 보상한다** — 관리가 잘 된 S2 는 얼마든지 있다.
"""
from __future__ import annotations

import importlib.util
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from lloydk.proxy_corpus import _DIRECT_GRADE_MARKER, grade_from_svm  # noqa: E402

import eval_fact_pools as EP  # noqa: E402
import v6_fact_pools as TP  # noqa: E402


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "v3_builder", _SCRIPTS / "build_direct_authored_proxy_eval_v3.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v3 = _load_builder()


def _sentences(pools):
    out = set()
    for factor, levels in pools.items():
        for level, pool in levels.items():
            for quote, tail in pool:
                out.add(" ".join((quote + tail).split()))
    return out


def test_eval_and_train_pools_share_no_sentence():
    """핵심 — 한 문장이라도 겹치면 평가셋이 암기량을 잰다."""
    shared = _sentences(EP.POOLS) & _sentences(TP.POOLS)
    assert not shared, f"학습셋 풀과 겹치는 문장: {sorted(shared)[:3]}"


def test_eval_and_train_pools_share_no_quote_fragment():
    """인용 조각도 겹치면 안 된다 — evidence span 이 가리키는 부분이다."""
    def quotes(pools):
        return {q for levels in pools.values() for pool in levels.values() for q, _t in pool}

    assert not (quotes(EP.POOLS) & quotes(TP.POOLS))


def test_eval_and_train_neutral_notes_are_disjoint():
    """길이 보정 문단이 공유되면 그것부터가 문장 공유다 — 전 문서에 깔리므로 영향이 크다."""
    train_notes = {" ".join(n.split()) for n in TP.__dict__.get("NEUTRAL_NOTES", ())}
    if not train_notes:  # 학습 풀은 빌더가 노트를 들고 있다
        builder = importlib.util.spec_from_file_location(
            "v6_builder", _SCRIPTS / "build_direct_authored_catalog_training_corpus_v6.py"
        )
        module = importlib.util.module_from_spec(builder)
        builder.loader.exec_module(module)
        train_notes = {" ".join(n.split()) for n in module.NEUTRAL_NOTES}
    eval_notes = {" ".join(n.split()) for n in EP.NEUTRAL_NOTES}
    assert not (eval_notes & train_notes)


def test_grade_combination_pool_matches_the_authoritative_rule():
    """조합 풀은 grade_from_svm 에서 직접 열거한다 — 학습셋에서 경험적으로 뽑지 않는다.

    학습셋에서 뽑으면 평가셋이 학습셋의 조합 선택까지 물려받아 그 자체가 계보 공유가 된다.
    """
    pool = v3._grade_combination_pool()
    seen = 0
    for grade, combos in pool.items():
        for management, secrecy, value in combos:
            assert grade_from_svm(secrecy, value, management) == grade
            seen += 1
    assert seen == 27, "27개 삼중항이 전부 열거돼야 한다"


def test_reassignment_never_changes_the_grade():
    """등급은 원본 그대로 두고 조합만 바꾼다 — 정답을 바꾸면 평가셋이 아니다."""
    pool = v3._grade_combination_pool()
    for grade in ("TS", "S1", "S2", "S3"):
        for ordinal in range(8):
            row = {"doc_id": f"d-{ordinal}", "label": grade}
            scores = v3._reassign_scores(row, pool, ordinal)
            assert (
                grade_from_svm(scores["secrecy"], scores["value"], scores["management"])
                == grade
            )


def test_reassignment_mixes_factor_levels_across_grades():
    """v2_2 의 결함이 되돌아오지 않게 — 요인 수준이 한 등급에 갇히면 안 된다.

    규칙상 secrecy=0·value=0 은 S3 에서만 성립하므로 그 둘은 예외로 둔다(진짜 판별 사실).
    """
    pool = v3._grade_combination_pool()
    counts = {"management": defaultdict(Counter), "secrecy": defaultdict(Counter),
              "value": defaultdict(Counter)}
    ordinal = Counter()
    for grade, n in (("TS", 200), ("S1", 250), ("S2", 250), ("S3", 300)):
        for _ in range(n):
            row = {"doc_id": "x", "label": grade}
            scores = v3._reassign_scores(row, pool, ordinal[grade])
            ordinal[grade] += 1
            for factor in counts:
                counts[factor][scores[factor]][grade] += 1

    exempt = {("secrecy", 0), ("value", 0)}
    for factor, levels in counts.items():
        for level, dist in levels.items():
            if (factor, level) in exempt:
                assert set(dist) == {"S3"}      # 규칙상 그렇다는 것을 못박는다
                continue
            top = dist.most_common(1)[0][1] / sum(dist.values())
            assert top < 0.95, f"{factor}={level} 이 사실상 한 등급 전용이다: {dict(dist)}"


def test_quotes_are_contract_valid():
    for factor, levels in EP.POOLS.items():
        for level, pool in levels.items():
            for quote, _tail in pool:
                assert 12 <= len(quote) <= 240, f"{factor}/{level}: {len(quote)}자"
                assert not _DIRECT_GRADE_MARKER.search(quote), quote


def test_quotes_unique_within_the_eval_pool():
    seen = set()
    for factor, levels in EP.POOLS.items():
        for level, pool in levels.items():
            for quote, _tail in pool:
                assert quote not in seen, f"중복 인용: {quote!r}"
                seen.add(quote)


def test_s3_only_levels_have_enough_paraphrases():
    """secrecy=0·value=0 은 규칙상 S3 전용이다. 문자열 암기만 막는다(각 변형 < 5%)."""
    assert len(EP.POOLS["nonpublicity"][0]) >= 12
    assert len(EP.POOLS["competitive_value"][0]) >= 16


def test_adjudication_sync_keeps_audit_consistent_with_the_new_scores():
    """감사기록을 안 맞추면 proxy_corpus 가 거부한다(실측 579/1,000)."""
    row = {"consensus_evidence": {"primary_sample_count": 3, "gate_status": "gold_candidate"}}
    scores = {"management": 1, "secrecy": 2, "value": 2}
    audit = v3._sync_adjudication(row, scores)
    assert audit["primary_factor_scores"] == scores
    assert audit["primary_factor_votes"]["secrecy"] == {"2": 3}
    assert audit["primary_factor_coverage"] == {"secrecy": 3, "value": 3, "management": 3}
    assert audit["gate_status"] == "gold_candidate"      # 나머지 필드는 보존

"""ConsensusJudge self-consistency + 섀도 + 거버넌스 팩토리 테스트.

주입한 가짜 LLMLabeler(스크립트 등급)로 결정성·비용 동작을 고정. 실 provider 호출 없음.
"""
import pytest

from lloydk.modules.m3_labeling.judge import ConsensusJudge, build_consensus_judge


class _Prov:
    def __init__(self, name: str) -> None:
        self.name = name


class _Res:
    def __init__(self, grade: str, factor_scores=None) -> None:
        self.grade, self.confidence, self.rationale = grade, 0.9, "r"
        self.factor_scores = (
            {"secrecy": 1, "value": 1, "management": 1}
            if factor_scores is None
            else factor_scores
        )


class _FakeLabeler:
    """LLMLabeler 덕타이핑: .label(text, *, temperature) + .provider.name."""

    def __init__(self, grades, name: str = "anthropic", factors=None) -> None:
        self._g = list(grades)
        self._factors = factors
        self.calls = 0
        self.provider = _Prov(name)

    def label(self, text, *, max_tokens: int = 1500, temperature: float = 0.1):
        g = self._g[self.calls] if self.calls < len(self._g) else self._g[-1]
        self.calls += 1
        factors = None
        if self._factors is not None:
            factors = (
                self._factors[self.calls - 1]
                if self.calls - 1 < len(self._factors)
                else self._factors[-1]
            )
        return _Res(g, factors)


def test_majority_vote_and_self_consistency():
    j = ConsensusJudge(primary=_FakeLabeler(["S1", "S1", "S2"]), k_min=3, k_max=3)
    out = j.judge("doc")
    assert out.grade == "S1"
    assert out.self_consistency == pytest.approx(2 / 3)


def test_adaptive_k_stops_on_unanimous():
    f = _FakeLabeler(["S1", "S1", "S1", "S1", "S1"])
    ConsensusJudge(primary=f, k_min=3, k_max=5).judge("doc")
    assert f.calls == 3                      # 만장일치 → 추가 샘플 안 함


def test_adaptive_k_escalates_on_split():
    f = _FakeLabeler(["S1", "S2", "S1", "S1", "S1"])
    out = ConsensusJudge(primary=f, k_min=3, k_max=5).judge("doc")
    assert f.calls == 5                      # 갈리면 k_max까지
    assert out.grade == "S1"                 # 4×S1 vs 1×S2


def test_adaptive_k_escalates_on_missing_or_split_factor_votes():
    factors = [
        {"secrecy": 1, "value": 1, "management": 1},
        {"secrecy": 1, "value": 2},
        {"secrecy": 1, "value": 1, "management": 1},
        {"secrecy": 1, "value": 1, "management": 1},
        {"secrecy": 1, "value": 1, "management": 1},
    ]
    fake = _FakeLabeler(["S2"] * 5, factors=factors)
    out = ConsensusJudge(primary=fake, k_min=3, k_max=5).judge("doc")

    assert fake.calls == 5
    assert out.sample_count == 5
    assert out.factor_votes["value"] == {1: 4, 2: 1}
    assert out.factor_coverage["management"] == 4


def test_ordinary_judge_contract_does_not_request_proxy_quality_fields():
    # _FakeLabeler deliberately has no include_document_quality keyword. If the
    # ordinary path changes its call contract this test raises TypeError.
    out = ConsensusJudge(
        primary=_FakeLabeler(["S2", "S2"]), k_min=2, k_max=2
    ).judge("doc")

    assert out.document_quality_required is False
    assert out.quality_samples == []
    assert out.quality_votes == {}


def test_proxy_quality_is_collected_in_the_same_primary_samples():
    checks = (
        "structure_appropriate",
        "timeline_consistent",
        "quantitative_consistent",
        "non_repetitive",
    )

    class _ProxyLabeler(_FakeLabeler):
        def label(
            self,
            text,
            *,
            max_tokens: int = 1500,
            temperature: float = 0.1,
            include_document_quality: bool = False,
        ):
            assert include_document_quality is True
            result = super().label(
                text, max_tokens=max_tokens, temperature=temperature
            )
            result.quality_checks = {check: True for check in checks}
            result.quality_issues = []
            return result

    labeler = _ProxyLabeler(["S2", "S2"])
    out = ConsensusJudge(
        primary=labeler,
        k_min=2,
        k_max=2,
        require_document_quality=True,
    ).judge("doc")

    assert labeler.calls == 2
    assert out.document_quality_required is True
    assert len(out.quality_samples) == 2
    assert all(out.quality_votes[check] == {True: 2} for check in checks)


def test_fnr_safe_tiebreak_picks_higher_grade():
    out = ConsensusJudge(primary=_FakeLabeler(["TS", "S1"]), k_min=2, k_max=2).judge("doc")
    assert out.grade == "TS"                 # 동점 → 상위등급(낮은 GRADE_RANK)
    assert out.self_consistency == pytest.approx(0.5)


def test_shadow_called_only_when_commercial_primary():
    shadow = _FakeLabeler(["S3"], name="vllm")
    out = ConsensusJudge(
        primary=_FakeLabeler(["S2", "S2", "S2"]), shadow=shadow, airgap=False, k_min=3, k_max=3,
    ).judge("doc")
    assert out.shadow_grade == "S3" and shadow.calls == 1


def test_shadow_skipped_in_airgap():
    shadow = _FakeLabeler(["S3"], name="vllm")
    out = ConsensusJudge(
        primary=_FakeLabeler(["S2", "S2", "S2"]), shadow=shadow, airgap=True, k_min=3, k_max=3,
    ).judge("doc")
    assert out.shadow_grade is None and shadow.calls == 0


def test_all_parse_fail_forces_review():
    out = ConsensusJudge(primary=_FakeLabeler(["XX", "??", "--"]), k_min=3, k_max=3).judge("doc")
    assert out.grade == "S3" and out.self_consistency == 0.0


def test_mixed_parse_failure_is_retained_in_vote_audit():
    out = ConsensusJudge(
        primary=_FakeLabeler(["S2", "XX", "S2"]), k_min=3, k_max=3
    ).judge("doc")

    assert out.grade == "S2"
    assert out.votes == {"S2": 2, "PARSE_FAIL": 1}
    assert out.sample_count == 3


def test_build_consensus_judge_noop_forces_k1():
    j = build_consensus_judge(primary_name="noop")
    assert j.k_min == 1 and j.k_max == 1
    assert j.airgap is True and j.shadow is None

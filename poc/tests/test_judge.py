"""ConsensusJudge self-consistency + 섀도 + 거버넌스 팩토리 테스트.

주입한 가짜 LLMLabeler(스크립트 등급)로 결정성·비용 동작을 고정. 실 provider 호출 없음.
"""
import pytest

from lloydk.modules.m3_labeling.judge import ConsensusJudge, build_consensus_judge


class _Prov:
    def __init__(self, name: str) -> None:
        self.name = name


class _Res:
    def __init__(self, grade: str) -> None:
        self.grade, self.confidence, self.rationale = grade, 0.9, "r"


class _FakeLabeler:
    """LLMLabeler 덕타이핑: .label(text, *, temperature) + .provider.name."""

    def __init__(self, grades, name: str = "anthropic") -> None:
        self._g = list(grades)
        self.calls = 0
        self.provider = _Prov(name)

    def label(self, text, *, max_tokens: int = 1500, temperature: float = 0.1):
        g = self._g[self.calls] if self.calls < len(self._g) else self._g[-1]
        self.calls += 1
        return _Res(g)


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


def test_build_consensus_judge_noop_forces_k1():
    j = build_consensus_judge(primary_name="noop")
    assert j.k_min == 1 and j.k_max == 1
    assert j.airgap is True and j.shadow is None

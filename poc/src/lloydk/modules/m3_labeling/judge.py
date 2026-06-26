"""ConsensusJudge — 주 판정자 self-consistency + Qwen 섀도 (P1 골든 게이트용).

기존 LLMLabeler를 **조립만** 한다(프롬프트·파싱 재구현 X). 핵심:
  - self-consistency : base.LLMProvider에 네이티브 n-샘플링이 없어 LLMLabeler.label()을 k회 호출,
                       다수결(FNR-safe 동점처리)로 grade, top_votes/k를 애매도 신호로.
  - adaptive-k       : k_min회 만장일치면 멈추고, 갈리면 k_max까지(비용 절감).
  - 섀도(Qwen)       : 상용이 주판정일 때만 1회. 불일치는 production_suspect로 사람큐에 surfacing(투표자 아님).
  - airgap           : 상용 미연동/민감문서 → Qwen 주판정·섀도 없음·상용 호출 0(공개 클라우드로 비밀 안 보냄).

conf는 수집만 하되 admission엔 안 쓰고 정렬(sort_conf)로만 넘긴다(실측 AUROC 0.535).
JudgeResult를 consensus.evaluate_consensus의 인자(self_consistency·shadow_grade·airgap)로 넘기는 것은
호출부(golden_builder.make_label_fn / make_llm_judge_gold; P1b) 책임이다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from lloydk.adapters.llm import build_provider
from lloydk.modules.m3_labeling.consensus import GRADE_RANK
from lloydk.modules.m3_labeling.llm_labeler import LLMLabeler

_PARSE_FAIL = "PARSE_FAIL"
# 상용(공개 클라우드) provider — 민감문서는 여기로 보내지 않는다(거버넌스).
COMMERCIAL = {"anthropic", "openai", "google", "gemini"}


@dataclass(frozen=True)
class JudgeResult:
    grade: str                 # 주 판정자 k샘플 다수표(동점=FNR-safe 상위등급)
    self_consistency: float    # top_votes / k  — 애매도 신호(1.0=만장일치)
    votes: dict                # {'S1': 4, 'S2': 1}  (PARSE_FAIL은 분모에서 제외)
    mean_conf: float           # 평균 자가보고 conf → 정렬(sort_conf) 전용
    shadow_grade: str | None   # Qwen 섀도 등급(None=섀도 없음/airgap)
    airgap: bool
    primary_provider: str
    rationale: str             # 다수표 샘플의 근거
    usage: list = field(default_factory=list)


class ConsensusJudge:
    def __init__(
        self,
        *,
        primary: LLMLabeler,
        shadow: LLMLabeler | None = None,
        airgap: bool = False,
        k_min: int = 3,
        k_max: int = 5,
        temperature: float = 0.7,
    ) -> None:
        self.primary = primary
        self.shadow = shadow
        self.airgap = airgap
        self.k_min = max(1, int(k_min))
        self.k_max = max(self.k_min, int(k_max))
        self.temperature = float(temperature)

    def judge(self, text: str) -> JudgeResult:
        grades: list[str] = []
        confs: list[float] = []
        rationales: list[str] = []

        def _sample() -> None:
            r = self.primary.label(text, temperature=self.temperature)
            grades.append(r.grade if r.grade in GRADE_RANK else _PARSE_FAIL)
            confs.append(float(r.confidence))
            rationales.append(str(r.rationale))

        for _ in range(self.k_min):
            _sample()
        # adaptive-k: 의견이 갈릴 때만 추가 샘플(만장일치면 commercial 호출 절감).
        if len(set(grades)) > 1:
            for _ in range(self.k_max - self.k_min):
                _sample()

        graded = [(g, c, rat) for g, c, rat in zip(grades, confs, rationales) if g != _PARSE_FAIL]
        counts = Counter(g for g, _, _ in graded)
        if not counts:
            # 전부 파싱 실패 → 강제 사람검토(S3 placeholder, self_consistency=0).
            return JudgeResult(
                grade="S3", self_consistency=0.0, votes={_PARSE_FAIL: len(grades)},
                mean_conf=0.0, shadow_grade=None, airgap=self.airgap,
                primary_provider=self._provider_name(), rationale="", usage=[],
            )

        top = max(counts.values())
        # FNR-safe 동점처리: 동점이면 더 높은 등급(낮은 GRADE_RANK). rule_engine 정책과 동일.
        grade = min((g for g, c in counts.items() if c == top), key=lambda g: GRADE_RANK[g])
        sc = top / len(graded)
        mean_conf = sum(c for _, c, _ in graded) / len(graded)
        rationale = next((rat for g, _, rat in graded if g == grade), "")

        shadow_grade = None
        if self.shadow is not None and not self.airgap:
            sr = self.shadow.label(text, temperature=0.1)  # 섀도는 결정론적 1회
            shadow_grade = sr.grade if sr.grade in GRADE_RANK else None

        return JudgeResult(
            grade=grade, self_consistency=sc, votes=dict(counts), mean_conf=mean_conf,
            shadow_grade=shadow_grade, airgap=self.airgap,
            primary_provider=self._provider_name(), rationale=rationale, usage=[],
        )

    def _provider_name(self) -> str:
        return getattr(getattr(self.primary, "provider", None), "name", "unknown")


def build_consensus_judge(
    *,
    primary_name: str | None = None,
    sensitive: bool = False,
    settings=None,
) -> ConsensusJudge:
    """provider 선택 + 데이터 거버넌스 라우팅으로 ConsensusJudge 조립.

    - primary_name = 상용 택일('anthropic'|'gemini'|'openai') 또는 settings.judge_primary_provider.
    - sensitive(실고객 비밀)거나 비-상용이면 → airgap(Qwen 주판정, 섀도·상용 호출 0).
      공개 클라우드 API엔 민감문서를 보내지 않는다.
    - noop(CI/드라이런) → k=1 결정론적·무료.
    """
    if settings is None:
        from lloydk.config import settings as _s  # noqa: PLC0415
        settings = _s
    primary_name = (primary_name or getattr(settings, "judge_primary_provider", "anthropic")).lower()
    onprem = getattr(settings, "judge_shadow_provider", "vllm") or "vllm"
    temperature = float(getattr(settings, "judge_temperature", 0.7))

    if primary_name == "noop":
        return ConsensusJudge(
            primary=LLMLabeler(provider=build_provider("noop")),
            shadow=None, airgap=True, k_min=1, k_max=1, temperature=temperature,
        )

    if sensitive or primary_name not in COMMERCIAL:
        # 거버넌스: 민감/비-상용 → 온프렘 Qwen 단독, 섀도 없음.
        primary = LLMLabeler(provider=build_provider(onprem))
        shadow, airgap = None, True
    else:
        primary = LLMLabeler(provider=build_provider(primary_name))
        shadow = (
            LLMLabeler(provider=build_provider(onprem))
            if getattr(settings, "judge_shadow_enabled", True) else None
        )
        airgap = False

    k_min = int(getattr(settings, "judge_k_min", 3))
    k_max = int(getattr(settings, "judge_k_max", 5))
    if getattr(getattr(primary, "provider", None), "name", "") == "noop":
        k_min = k_max = 1  # provider가 noop으로 떨어지면 결정론적
    return ConsensusJudge(
        primary=primary, shadow=shadow, airgap=airgap,
        k_min=k_min, k_max=k_max, temperature=temperature,
    )

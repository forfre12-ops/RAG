"""constructed_floor — 구성적 witness 라벨의 **floor 전용** 결정적 검증기 (사람검수 0).

기원(2026-07-03 5기둥 적대검증): '구성적 witness 라벨' 기둥은 exact-grade로는 붕괴
(상향오염=열린집합이라 '더 높은 등급 사실의 부재'를 기계로 증명 불가)하고, **grade-floor
(≥등급)** 주장으로 격하하면 생존한다. 이 모듈은 그 생존 조건을 코드로 강제한다:

  1. witness 존재 검증 — 등급을 결정하는 사실(witness 토큰)이 본문에 **긍정 문맥으로**
     존재하는가. 공격에서 확인된 기존 check_fact_preservation의 두 갭을 고쳤다:
       (a) 첫 출현만 검사(text.find) → **전 출현 스캔**
       (b) 부정 윈도 20자 → **문단 수준 공개선언 스캔**("아래는 특허 공개공보에 게재된
           내용이다: … [witness]" 같은 장거리 공개 문맥이 비공지 전제를 깨는 것을 탐지)
  2. 상위등급 negative scan — S1/S2/S3 의도 문서에 상위등급 어휘(KEYWORD_SEEDS의 더
     비밀 등급 시드)가 하나라도 있으면 탈락. 상향오염의 **결정적 부분 차단**이다 —
     열린집합 잔여 오염은 원리상 남으며(그래서 floor), 0% 오염 주장은 금지.
  3. 권위 계층화 — 모든 witness는 근거(basis: 법령/taxonomy 인용) 필수. TS witness는
     외부 열거(§9 고시·방위산업기술보호법 등) 인용이 있어야 한다(자사 정의만으로 TS
     floor를 주장하지 않는다).
  4. lexicon disjoint 가드 — 평가용 witness 표면형이 학습 코퍼스에 나타나면 안 된다
     (witness 토큰이 학습에 새면 평가가 witness-token 검출력 측정으로 퇴화하는 shortcut
     자기충족 차단). CI에서 witness_lexicon_disjoint()로 검사.

산출물 의미론(오독 금지):
  - tier 명칭은 constructed_floor_eval — locked_gold_eval(사람서명)과 절대 혼동 금지.
  - 라벨은 '최소 이 등급'(floor)이다. exact-grade·실문서 성능 근거로 인용 금지
    (합성 분포 안 floor 회귀: '명백 케이스에서조차 미탐하면 FAIL' 전용).
  - 심판 LLM은 이 검증기 어디에도 없다(순환 차단) — 심판은 별도 negative filter로만.

순수 모듈: I/O·DB·LLM 없음, 전부 결정적 문자열 검사(단위테스트 가능).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

TIER_CONSTRUCTED_FLOOR = "constructed_floor_eval"
LABEL_SOURCE_CONSTRUCTED_FLOOR = "constructed_floor"
TRUTH_WARNING = (
    "constructed_floor — grade-floor(≥등급)만 기계 증명됨. exact-grade 아님·"
    "실문서 성능 보증 아님·사람서명(locked_gold_eval) 아님."
)

# 부정 문맥(존재 부정) — metamorphic와 동일 계열, 전 출현에 적용.
DEFAULT_NEGATION_MARKERS = (
    "아니", "아닌", "않", "없", "미해당", "해당 없", "해당없", "비해당", "제외",
    "not ", "n't", "no ", "non-", "없음",
)
DEFAULT_NEGATION_WINDOW = 20

# 문단 수준 공개선언 마커 — witness가 이 문맥의 문단에 있으면 비공지 전제가 깨진다.
# (20자 윈도가 못 잡는 장거리 선언: "아래 항목은 이미 특허 공개공보에 게재된 내용이다: …")
DEFAULT_PUBLIC_CONTEXT_MARKERS = (
    "공개공보", "이미 공개", "이미 공지", "일반에 공개", "대외 공개", "공개된 자료",
    "보도자료", "언론에 배포", "홈페이지에 게시", "웹사이트에 게시", "공시된",
    "누구나 열람", "특허 공개", "공개 특허", "published patent", "publicly available",
    "press release",
)

# TS floor의 권위 계층화 — 외부 열거 인용이 basis에 있어야 한다(자사 정의만으로 TS 금지).
TS_EXTERNAL_BASIS_MARKERS = (
    "§9", "제9조", "고시", "국가핵심기술", "방위산업기술보호법", "산업기술보호법",
)

_GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}  # 낮을수록 더 비밀


def find_occurrences(text: str, token: str) -> list[int]:
    """token의 **전 출현** 시작 인덱스 — 첫 출현만 보던 갭(공격 발견)의 수정."""
    if not token:
        return []
    out: list[int] = []
    start = 0
    while True:
        idx = text.find(token, start)
        if idx < 0:
            return out
        out.append(idx)
        start = idx + 1


def _negation_near(text: str, start: int, end: int,
                   markers: Sequence[str], window: int) -> bool:
    seg = text[max(0, start - window): end + window].lower()
    return any(m.lower() in seg for m in markers)


def _paragraph_of(text: str, idx: int) -> str:
    """idx가 속한 문단(빈 줄 기준) — 문단 수준 공개선언 스캔용."""
    start = text.rfind("\n\n", 0, idx)
    start = 0 if start < 0 else start + 2
    end = text.find("\n\n", idx)
    end = len(text) if end < 0 else end
    return text[start:end]


@dataclass
class WitnessCheck:
    """witness 1건의 결정적 검증 결과."""

    token: str
    present: bool = False            # ≥1 출현이 부정 문맥 밖에 존재
    publicity_broken: bool = False   # 어떤 출현이든 공개선언 문단 안에 있음(비공지 붕괴)
    occurrences: int = 0

    @property
    def valid(self) -> bool:
        return self.present and not self.publicity_broken


def check_witness(
    text: str,
    token: str,
    *,
    negation_markers: Sequence[str] = DEFAULT_NEGATION_MARKERS,
    negation_window: int = DEFAULT_NEGATION_WINDOW,
    public_markers: Sequence[str] = DEFAULT_PUBLIC_CONTEXT_MARKERS,
) -> WitnessCheck:
    """witness 토큰 검증 — 전 출현 스캔 + 부정 문맥 + 문단 공개선언.

    present    : 출현 중 하나라도 부정 문맥에 안 묶이면 True(사실이 긍정 서술됨).
    publicity_broken: 출현 중 하나라도 공개선언 문단 안이면 True — 그 사실이 공개로
                 선언된 순간 '비공지' 전제가 깨지므로 다른 깨끗한 출현이 있어도 무효.
    """
    occs = find_occurrences(text, token)
    res = WitnessCheck(token=token, occurrences=len(occs))
    if not occs:
        return res
    lowered_markers = [m.lower() for m in public_markers]
    for idx in occs:
        if not _negation_near(text, idx, idx + len(token), negation_markers, negation_window):
            res.present = True
        para = _paragraph_of(text, idx).lower()
        if any(m in para for m in lowered_markers):
            res.publicity_broken = True
    return res


def upper_grade_lexicon(
    intended_grade: str,
    seeds: Sequence[dict] | None = None,
) -> list[str]:
    """의도 등급보다 **엄격히 더 비밀**인 등급들의 시드 키워드 — negative scan 어휘.

    seeds 기본값은 룰엔진 KEYWORD_SEEDS(공개 법령 출처 시드) — 룰엔진을 '라벨 인증'이
    아니라 '탈락 전용(veto)'으로만 단방향 재사용한다(순환 없음: 통과가 아무것도 증명
    하지 않고, 히트만 탈락시킨다).
    """
    if seeds is None:
        from lloydk.modules.m3_labeling.seeds import KEYWORD_SEEDS  # noqa: PLC0415
        seeds = KEYWORD_SEEDS
    my_order = _GRADE_ORDER.get(str(intended_grade or "").upper())
    if my_order is None:
        return []
    return [
        s["keyword"] for s in seeds
        if _GRADE_ORDER.get(str(s.get("grade", "")).upper(), 99) < my_order and s.get("keyword")
    ]


def upper_grade_negative_scan(
    text: str,
    intended_grade: str,
    *,
    seeds: Sequence[dict] | None = None,
    extra_lexicon: Sequence[str] = (),
) -> list[str]:
    """상위등급 어휘 히트 목록 — 히트 1개라도 있으면 호출부는 탈락시켜야 한다.

    상향오염(의도 S2 문서에 TS급 신호 혼입)의 결정적 부분 차단. 어휘 밖 상향오염은
    원리상 못 잡는다(열린집합) — 그래서 이 트랙의 라벨은 floor다.
    """
    lex = list(upper_grade_lexicon(intended_grade, seeds)) + list(extra_lexicon)
    lowered = text.lower()
    return sorted({k for k in lex if k and k.lower() in lowered})


@dataclass
class FloorVerdict:
    """구성적 floor 레코드 1건의 admission 판정 — 실패만 정보(통과=품질 아님)."""

    admitted: bool
    grade_floor: str | None = None
    label_kind: str = "floor"
    reasons: list[str] = field(default_factory=list)
    witness_checks: list[WitnessCheck] = field(default_factory=list)
    upper_grade_hits: list[str] = field(default_factory=list)
    truth_warning: str = TRUTH_WARNING

    def to_dict(self) -> dict:
        return {
            "admitted": self.admitted,
            "grade_floor": self.grade_floor,
            "label_kind": self.label_kind,
            "reasons": self.reasons,
            "witness_checks": [
                {"token": w.token, "present": w.present,
                 "publicity_broken": w.publicity_broken, "occurrences": w.occurrences}
                for w in self.witness_checks
            ],
            "upper_grade_hits": self.upper_grade_hits,
            "truth_warning": self.truth_warning,
        }


def evaluate_constructed_floor(
    record: dict,
    *,
    seeds: Sequence[dict] | None = None,
    extra_upper_lexicon: Sequence[str] = (),
) -> FloorVerdict:
    """구성적 floor 레코드의 결정적 admission.

    record 계약: {text, intended_grade, witnesses: [{token, basis}, ...]}
      - witnesses 비면 탈락(검사 불가 — 무근거 floor 주장 금지)
      - 모든 witness: basis(법령/taxonomy 인용) 필수 + check_witness 통과
      - TS 의도: basis에 외부 열거 인용(TS_EXTERNAL_BASIS_MARKERS) 필수 — 자사 정의만으로
        TS floor 주장 금지(권위 계층화)
      - 비-TS 의도: 상위등급 negative scan 무히트
    통과 시 grade_floor=의도 등급(**floor**) — exact-grade 주장이 아니다.
    """
    text = str(record.get("text") or "")
    grade = str(record.get("intended_grade") or "").upper()
    witnesses = list(record.get("witnesses") or [])
    reasons: list[str] = []

    if grade not in _GRADE_ORDER:
        return FloorVerdict(admitted=False, reasons=[f"invalid_grade:{grade!r}"])
    if not text.strip():
        return FloorVerdict(admitted=False, reasons=["empty_text"])
    if not witnesses:
        return FloorVerdict(admitted=False, reasons=["no_witnesses(무근거 floor 주장 금지)"])

    checks: list[WitnessCheck] = []
    for w in witnesses:
        token = str(w.get("token") or "")
        basis = str(w.get("basis") or "").strip()
        chk = check_witness(text, token)
        checks.append(chk)
        if not basis:
            reasons.append(f"witness_no_basis:{token!r}")
        if not chk.present:
            reasons.append(f"witness_missing_or_negated:{token!r}")
        if chk.publicity_broken:
            reasons.append(f"witness_publicity_broken:{token!r}")
        if grade == "TS" and basis and not any(m in basis for m in TS_EXTERNAL_BASIS_MARKERS):
            reasons.append(f"ts_basis_not_external:{token!r}")

    upper_hits: list[str] = []
    if grade != "TS":
        upper_hits = upper_grade_negative_scan(
            text, grade, seeds=seeds, extra_lexicon=extra_upper_lexicon
        )
        if upper_hits:
            reasons.append(f"upper_grade_contamination:{upper_hits[:5]}")

    admitted = not reasons
    return FloorVerdict(
        admitted=admitted,
        grade_floor=grade if admitted else None,
        reasons=reasons,
        witness_checks=checks,
        upper_grade_hits=upper_hits,
    )


def witness_lexicon_disjoint(
    witness_tokens: Sequence[str],
    corpus_texts: Sequence[str],
) -> list[dict]:
    """평가 witness 표면형이 학습 코퍼스에 나타나는지 검사 — offender 목록 반환.

    비어 있지 않으면 CI가 실패해야 한다: witness 토큰이 train/eval 양쪽에 있으면
    분류기가 witness 표면형을 지름길 학습하고 평가는 그 검출력 측정으로 퇴화한다
    (도메인→등급 누출 85.6% 사건과 같은 계열의, 더 정밀한 누출).
    """
    offenders: list[dict] = []
    for tok in witness_tokens:
        if not tok:
            continue
        hits = sum(1 for t in corpus_texts if tok in (t or ""))
        if hits:
            offenders.append({"token": tok, "train_hits": hits})
    return offenders

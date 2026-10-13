"""S·V·M 을 사람이 절차대로 판단할 때 쓰는 계산기 — 자동 분류기가 아니다.

배경: 실문서에 S·V·M 을 매길 때 직감으로 등급을 먼저 찍고 요소를 거꾸로 채우면(=역산)
근거 없는 라벨이 된다([[m-metadata-absent-everywhere-2026-09-10]] 등에서 반복 확인된
함정). 이 모듈은 **반대 방향**을 강제한다 — 사람이 가이드 기준표를 보고 S·V·M 값을
먼저 정하면, 등급은 여기서 공식으로 계산한다.

정의 출처: `doc/[참고] 영업비밀 등급분류 가이드.pdf` 11쪽(원문 화면 확인, 2026-09-16).
등급 계산: 12쪽 순수곱셈(`grade_from_svm(mode="guide")`)을 그대로 쓴다.

M 이 확인 안 됐을 때: 가이드 11쪽 각주 "기업 규모, 실정 등에 따라 비공지성 또는 경제적
유용성 값만을 기준으로 분류 가능"을 근거로, M=0 을 강제로 채우지 않는다(순수곱셈에서
M=0 은 결과를 항상 0=공개로 뭉갠다 — `test_grade_formula_mode_is_noop.py` 옆의
`compare_guide_vs_code_formula` 실측으로 확인됨). 대신 S 단독·V 단독 매핑 중 **더 민감한
쪽**을 취한다(FNR-safe). 이 결합 방식은 가이드 원문에 없는 이 모듈의 설계 판단이다 —
발주처 승인 전에는 "우리 제안"으로만 인용할 것.

이 모듈은 텍스트를 읽지 않는다. 사람(또는 상위 절차)이 이미 0/1/2 로 판단한 값을 받아
등급만 계산한다 — 자동 라벨링이 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from koipa.modules.m3_labeling.rule_engine import grade_from_svm
from koipa.modules.m3_labeling.seeds import GRADE_ORDER

FactorLevel = Literal[0, 1, 2]

# ── 가이드 11쪽 정의 원문 그대로 ──────────────────────────────────────────
SECRECY_SCALE: dict[int, dict[str, object]] = {
    0: {"정의": "불특정 다수에게 공개된 정보", "예시": ("학위논문", "특허정보")},
    1: {"정의": "통상적인 방법으로 입수하기 어려운 정보", "예시": ("제안서", "계약서")},
    2: {"정의": "보유자를 통하지 않으면 알 수 없는 정보", "예시": ("연구개발 결과", "신규사업 투자계획")},
}

VALUE_SCALE: dict[int, dict[str, object]] = {
    0: {"정의": "경제적 가치가 없는 정보", "예시": ("소모품 내역",)},
    1: {"정의": "취득/개발에 비용 또는 노력이 투입된 정보", "예시": ("시장조사 보고서",)},
    2: {"정의": "상당한 비용 또는 노력이 투입된 정보", "예시": ("고객정보", "설계도면", "소스코드")},
}

MANAGEMENT_SCALE: dict[int, dict[str, object]] = {
    0: {"정의": "비밀로 관리할 수 없거나 임직원이라면 모두 알아야 하는 정보",
        "예시": ("홍보자료", "신고의무 정보")},
    1: {"정의": "업무상 필요한 자에게만 공개하는 정보", "예시": ("실험 데이터",)},
    2: {"정의": "승인된 자에 한해 공개하는 정보", "예시": ("제품/공정 정보", "경영계획")},
}

# 단독 판단 매핑(가이드 각주 "S 또는 V 값만으로 분류 가능"의 구체화 — 우리 설계 판단).
# 0/1/2 세 값만으로는 TS(극비)에 못 닿는다 — 가이드 2단계 예시도 같은 한계를 가진다.
_SINGLE_FACTOR_MAP: dict[int, str] = {0: "S3", 1: "S2", 2: "S1"}


@dataclass(frozen=True)
class SvmProcedureResult:
    grade: str
    formula_used: Literal["guide_full_svm", "s_v_only_fallback"]
    s: int
    v: int
    m: int | None
    note: str


def _validate_level(name: str, value: int) -> int:
    value = int(value)
    if value not in (0, 1, 2):
        raise ValueError(f"{name} 은 0/1/2 여야 한다 (받은 값: {value})")
    return value


def evaluate(s: FactorLevel, v: FactorLevel, m: FactorLevel | None) -> SvmProcedureResult:
    """S·V 는 필수(항상 판단 가능하다는 전제), M 은 확인 안 됐으면 None.

    S·V 는 문서 출처·투입비용처럼 본문·메타데이터에서 판단할 근거가 있다고 보고
    필수로 받는다. M 은 실측상 공급 0% 인 값이라 None 을 허용한다
    ([[m-metadata-absent-everywhere-2026-09-10]]).
    """
    s = _validate_level("s(비공지성)", s)
    v = _validate_level("v(경제적 유용성)", v)

    if m is not None:
        m = _validate_level("m(비밀관리성)", m)
        grade = grade_from_svm(s, v, m, mode="guide")
        return SvmProcedureResult(
            grade=grade, formula_used="guide_full_svm", s=s, v=v, m=m,
            note=f"가이드 12쪽 순수곱셈 S×V×M={s*v*m} → {grade}",
        )

    s_only = _SINGLE_FACTOR_MAP[s]
    v_only = _SINGLE_FACTOR_MAP[v]
    grade = min((s_only, v_only), key=lambda g: GRADE_ORDER.get(g, 999))
    return SvmProcedureResult(
        grade=grade, formula_used="s_v_only_fallback", s=s, v=v, m=None,
        note=(f"M 미확인 — S단독={s_only} · V단독={v_only} 중 더 민감한 "
              f"{grade} 채택(가이드 11쪽 각주 근거, FNR-safe 결합은 우리 설계)"),
    )

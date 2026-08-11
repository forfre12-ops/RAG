"""홀드아웃이 학습셋과 정말 독립인가 — 비교를 하기 **전에** 센다.

왜 필요한가. 이 사업에서 실문서 골든셋은 구조적으로 존재할 수 없다(실데이터·검수·반출 0
확정, 실 TS 문서 0건). 그래서 모델 비교는 합성 홀드아웃으로 할 수밖에 없고, 그 홀드아웃이
학습셋과 계보상 겹치면 비교 자체가 무의미해진다 — 같은 생성기가 만든 셋에서 F1 0.99 를
받고 실문서에서 0.61 로 무너진 것이 정확히 그 사례다(2026-08-09 v4_3).

이 모듈이 세는 것은 셋이고, **세 가지가 다르다**:

1. **문서 중복**   같은 본문이 양쪽에 있는가. 가장 명백한 오염.
2. **가족 중복**   같은 document_family_id / scenario_id 에서 갈라져 나왔는가.
                   본문이 달라도 같은 시나리오면 사실상 같은 문제를 두 번 푸는 것이다.
3. **생성기 중복** authoring_method / generation_lineage 가 같은가. 여기가 겹치면 본문도
                   가족도 달라도 **같은 작문 습관**을 공유한다 — 모델은 그 습관을 배우고,
                   습관이 다른 실문서에서 무너진다. 실무에서 가장 놓치기 쉬운 축이다.

세 축이 모두 0 이어야 "계보 독립"이라고 부를 수 있다. 1·2 만 보고 독립이라 부르면
v4_3 과 같은 실패를 다시 겪는다.

그리고 독립성만으로는 부족하다. 홀드아웃 **자체**가 길이나 등급 전용 문장으로 답을
알려주면, 독립이어도 그 수치는 분류 능력의 증거가 아니다. 그래서 누출 계량을 같은
보고서에 **함께** 싣는다(lloydk.dataset_leakage) — 따로 내면 한쪽만 인용된다.

⚠ 이 보고서가 전부 통과해도 나올 수 있는 주장은 **"합성 내부 일관성 + 안전 무회귀"까지**다.
실 일반화 근거가 아니다 — 자체 실측으로 교차silver→gold_real F1 0.26 이 그 천장을 보여줬고,
그건 이번 검증이 부족해서가 아니라 데이터 정책이 만든 구조적 한계다. 실 일반화 증거는
고객사 현장 운영학습에서만 나온다.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable, Mapping, Sequence

from lloydk.dataset_leakage import audit

# 권고 상한. 게이트가 아니라 판단 기준이다 — 막는 것은 호출부가 정한다.
RECOMMENDED_MAX_LENGTH_LEAK = 0.55
RECOMMENDED_MAX_THEILS_U = 0.25
RECOMMENDED_MAX_TELL_COVERAGE = 0.10

_FAMILY_KEYS = ("document_family_id", "scenario_id", "family_profile_id")
_GENERATOR_KEYS = ("authoring_method", "primary_judge_model")


def _text(record: Mapping[str, Any]) -> str:
    return str(record.get("text") or "")


def _text_hash(record: Mapping[str, Any]) -> str:
    normalized = " ".join(_text(record).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _values(records: Sequence[Mapping[str, Any]], key: str) -> set[str]:
    out: set[str] = set()
    for record in records:
        value = record.get(key)
        if isinstance(value, (list, tuple)):
            out.update(str(v) for v in value if v)
        elif value:
            out.add(str(value))
    return out


def _overlap(left: set[str], right: set[str]) -> dict[str, Any]:
    shared = sorted(left & right)
    denominator = min(len(left), len(right)) or 1
    return {
        "train": len(left),
        "holdout": len(right),
        "shared": len(shared),
        "share_of_smaller": round(len(shared) / denominator, 4),
        "examples": shared[:5],
    }


def assess(
    train: Iterable[Mapping[str, Any]],
    holdout: Iterable[Mapping[str, Any]],
    *,
    label: str = "holdout",
) -> dict[str, Any]:
    """계보 독립성 + 홀드아웃 자체 누출을 한 보고서로 낸다.

    판정하지 않고 사실만 모은다 — 막을지는 호출부가 정한다(check_or_raise 와 같은 분담).
    """
    train_rows = [r for r in train if _text(r)]
    holdout_rows = [r for r in holdout if _text(r)]

    document = _overlap(
        {_text_hash(r) for r in train_rows}, {_text_hash(r) for r in holdout_rows}
    )
    family = {key: _overlap(_values(train_rows, key), _values(holdout_rows, key)) for key in _FAMILY_KEYS}
    generator = {
        key: _overlap(_values(train_rows, key), _values(holdout_rows, key))
        for key in (*_GENERATOR_KEYS, "generation_lineage")
    }

    holdout_leakage = audit((str(r.get("label") or ""), _text(r)) for r in holdout_rows)
    train_leakage = audit((str(r.get("label") or ""), _text(r)) for r in train_rows)

    independent = (
        document["shared"] == 0
        and all(v["shared"] == 0 for v in family.values())
        and all(v["shared"] == 0 for v in generator.values())
    )
    concerns: list[str] = []
    if document["shared"]:
        concerns.append(f"같은 본문 {document['shared']}건이 양쪽에 있다")
    for key, value in family.items():
        if value["shared"]:
            concerns.append(f"{key} {value['shared']}개가 겹친다 — 같은 시나리오를 두 번 푼다")
    for key, value in generator.items():
        if value["shared"]:
            concerns.append(
                f"{key} {value['shared']}개가 겹친다 — 같은 작문 습관을 공유한다"
                " (본문·가족이 달라도 습관은 배운다)"
            )
    if holdout_leakage.get("documents"):
        if holdout_leakage["length_only_1nn"] > RECOMMENDED_MAX_LENGTH_LEAK:
            concerns.append(
                f"홀드아웃 길이-only {holdout_leakage['length_only_1nn']:.3f}"
                f" > 권고 {RECOMMENDED_MAX_LENGTH_LEAK}"
            )
        if holdout_leakage.get("length_theils_u", 0.0) > RECOMMENDED_MAX_THEILS_U:
            concerns.append(
                f"홀드아웃 Theil's U {holdout_leakage['length_theils_u']:.3f}"
                f" > 권고 {RECOMMENDED_MAX_THEILS_U}"
            )
        if holdout_leakage["tell_coverage"] > RECOMMENDED_MAX_TELL_COVERAGE:
            concerns.append(
                f"홀드아웃 tell 커버 {holdout_leakage['tell_coverage']:.3f}"
                f" > 권고 {RECOMMENDED_MAX_TELL_COVERAGE}"
            )

    return {
        "label": label,
        "train_documents": len(train_rows),
        "holdout_documents": len(holdout_rows),
        "lineage_independent": independent,
        "overlap": {"document_text": document, "family": family, "generator": generator},
        "holdout_leakage": holdout_leakage,
        "train_leakage": train_leakage,
        "concerns": concerns,
        "usable_for_comparison": independent and not concerns,
        # 통과해도 이 문장을 넘는 주장은 할 수 없다. 보고서에 박아 둔다 —
        # 지표만 인용되고 한정이 떨어져 나가는 일이 반복됐다.
        "claim_ceiling": (
            "합성 내부 일관성과 안전 무회귀까지. 실문서 일반화 근거가 아니다"
            "(자체 실측: 교차silver→gold_real F1 0.26). 실 일반화 증거는 고객사 현장"
            " 운영학습에서만 나온다."
        ),
    }

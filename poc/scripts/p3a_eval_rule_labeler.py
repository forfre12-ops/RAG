"""M3 룰 라벨러 자가검증 — 합성된 등급별 미니 코퍼스에서 정확도 측정.

각 등급별 시나리오 텍스트를 만들어 LabelingPipeline에 통과시키고
예측 등급 vs ground truth 일치도, FNR(고등급→저등급 미탐) 측정.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from lloydk.modules.m3_labeling import LabelingPipeline  # noqa: E402
from lloydk.modules.m3_labeling.seeds import GRADE_ORDER  # noqa: E402

# 등급별 검증 시나리오 — 각 등급의 전형적 문장 패턴 (가이드라인 기반)
SCENARIOS: list[dict] = [
    # TS
    {"truth": "TS", "text": "본 문서는 특급기밀 등급으로 분류된다. 차세대 제품 설계도와 핵심 원천기술의 상세 사양을 포함하며, M&A 계획과 회사 매각 협상 진행 상황을 다룬다."},
    {"truth": "TS", "text": "Top Secret. 임직원 인사 이동과 관련된 극비 사항. 인수합병 대상 기업 실사 결과 및 신약 화합물 합성 경로 첨부."},
    {"truth": "TS", "text": "TS등급 자료. 핵심 원천기술의 차세대 제품 설계도 포함. 외부 유출 시 회사 존립이 위협받는다."},

    # S1
    {"truth": "S1", "text": "본 문서는 1급 비밀로 영업비밀에 해당한다. 공정 노하우와 원가 구조, 고객 데이터베이스 분석 결과를 포함한다."},
    {"truth": "S1", "text": "영업비밀 자료. 알고리즘 소스코드와 신제품 개발 로드맵, 특허 출원 계획을 정리한다. 마케팅 전략과 고객 정보 활용 방안 포함."},
    {"truth": "S1", "text": "1급 비밀로 분류된 공정 노하우 자료. 고객 정보 데이터베이스 구조와 원가 구조 분석."},

    # S2
    {"truth": "S2", "text": "본 자료는 대외비로 분류된다. 2급 자료로서 내부 검토 결과와 분기 매출 현황, 사업 계획 초안을 담는다. 거래처 명단 별첨."},
    {"truth": "S2", "text": "대외비 내부 자료. 조직 개편안과 예산 배정 검토, 공급망 분석 보고서. 분기 매출 추이 포함."},
    {"truth": "S2", "text": "2급 대외비. 내부 검토용 사업 계획 자료. 거래처 명단과 분기 매출, 예산 배정 검토 자료."},

    # S3
    {"truth": "S3", "text": "회사 소개 보도자료. 채용 공고와 이용약관을 외부 공지한다. FAQ와 사보 콘텐츠를 포함하는 공개 자료."},
    {"truth": "S3", "text": "공시 및 공고. 보도자료 배포용. 회사 소개 자료와 FAQ 모음. 외부 공지 가능."},
    {"truth": "S3", "text": "공개 가능한 사보 콘텐츠. 채용 공고와 이용약관, 외부 공지 자료 모음."},
]


def main() -> int:
    pipe = LabelingPipeline()
    results = []
    confusion: Counter = Counter()
    fnr_under = 0
    total_high = 0  # truth가 TS/S1인 사례 수

    for sc in SCENARIOS:
        out = pipe.label(sc["text"])
        pred = out.grade.value if hasattr(out.grade, "value") else str(out.grade)
        truth = sc["truth"]
        truth_order = GRADE_ORDER[truth]
        pred_order = GRADE_ORDER[pred]
        underclass = pred_order > truth_order  # 예측이 더 낮은 등급 = 미탐
        results.append(
            {
                "truth": truth,
                "pred": pred,
                "confidence": out.confidence,
                "underclass": underclass,
                "factors": {
                    "EV": out.factors.economic_value,
                    "NP": out.factors.non_publicity,
                    "ML": out.factors.management_level,
                    "LI": out.factors.leak_impact,
                },
            }
        )
        confusion[(truth, pred)] += 1
        if truth_order <= 2:  # TS or S1
            total_high += 1
            if underclass:
                fnr_under += 1

    total = len(SCENARIOS)
    correct = sum(1 for r in results if r["truth"] == r["pred"])
    accuracy = correct / total
    fnr = fnr_under / max(total_high, 1)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("\n--- Confusion (truth, pred) ---")
    for k, v in sorted(confusion.items()):
        print(f"  {k}: {v}")
    print(f"\nAccuracy: {accuracy:.3f} ({correct}/{total})")
    print(f"FNR (high→low underclass): {fnr:.3f} ({fnr_under}/{total_high})")
    print("PASS" if (accuracy >= 0.75 and fnr <= 0.05) else "FAIL — rule seeds need refinement")

    out_dir = _HERE.parent / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "m3_rule_self_eval.json").write_text(
        json.dumps(
            {
                "accuracy": accuracy,
                "fnr": fnr,
                "confusion": [{"truth": t, "pred": p, "count": c} for (t, p), c in confusion.items()],
                "details": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0 if (accuracy >= 0.75 and fnr <= 0.05) else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""gold_real의 pseudo-label 노이즈 정량화.

가설(LABELING_GUIDE 등급 기준): 비공지성(NON_PUBLICITY)이 결정적. 이미 공개된
법원/특허심판 판결문(판례)은 본문이 영업비밀을 논하든 S3다. pseudo-labeler가
본문 키워드(영업비밀/원가/기밀 등)를 보고 S1/S2로 올린 것은 노이즈다.

이 스크립트는 '판례로 식별되는 문서 중 S3가 아닌 비율'을 노이즈 추정치로 본다.
- 식별: source가 '판례'로 시작 OR 본문에 사법 문서 마커 2개 이상.
- 의도적 비-판례 출처(public_scenario, synthetic_grounded 등)는 제외.
- human_review/public_definitive(큐레이션 확정) 라벨은 노이즈 후보에서 제외.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

# build_review_assist와 동일한 사법 문서 마커 (중복 유지: 독립 실행/CI 단순화)
RULING_MARKERS = [
    "【주 문】", "【주문】", "【이 유】", "【이유】", "【청구취지】",
    "대법원", "특허법원", "특허심판원", "고등법원", "지방법원",
    "선고", "심결", "상고", "항고심판", "원심판결", "원심심결",
    "거절사정", "권리범위확인", "등록무효", "거절결정", "등록취소",
    "【원고", "【피고", "【심판청구인", "소송대리인", "변리사", "변호사",
]

# 노이즈 판정에서 제외할 label_source (큐레이션 확정 또는 사람 검수)
TRUSTED_SOURCES = {"human_review", "public_definitive"}
# 판례가 아닌 의도적 합성/시나리오 출처 (노이즈 분석 대상 아님)
NON_RULING_SOURCES = {"public_scenario", "synthetic_grounded"}


def _rows(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out


def _is_ruling(row: dict) -> bool:
    src = str(row.get("source", ""))
    if src in NON_RULING_SOURCES:
        return False
    if src.startswith("판례"):
        return True
    text = row.get("text", "")
    return sum(1 for m in RULING_MARKERS if m in text) >= 2


def analyze(rows: list[dict]) -> dict:
    ruling = [r for r in rows if _is_ruling(r)]
    # 노이즈 후보: 판례인데 라벨이 S3가 아니고, 신뢰 출처가 아닌 것
    candidates = [
        r for r in ruling
        if (r.get("label") or "").upper() != "S3"
        and r.get("label_source") not in TRUSTED_SOURCES
    ]
    by_ls: dict[str, Counter] = defaultdict(Counter)
    by_label: Counter = Counter()
    for r in candidates:
        lab = (r.get("label") or "").upper()
        by_label[lab] += 1
        by_ls[r.get("label_source", "?")][lab] += 1

    ruling_scored = [r for r in ruling if r.get("label_source") not in TRUSTED_SOURCES]
    n_ruling = len(ruling_scored)
    n_noise = len(candidates)
    return {
        "total": len(rows),
        "ruling_total": len(ruling),
        "ruling_scored": n_ruling,           # 신뢰출처 제외 판례
        "noise_count": n_noise,              # 판례인데 비-S3 (노이즈 추정)
        "noise_rate": round(n_noise / n_ruling, 4) if n_ruling else 0.0,
        "noise_by_label": dict(by_label.most_common()),
        "noise_by_label_source": {k: dict(v) for k, v in by_ls.items()},
        "examples": [
            {
                "doc_id": r.get("doc_id"),
                "label": r.get("label"),
                "label_source": r.get("label_source"),
                "source": r.get("source"),
                "preview": " ".join(r.get("text", "").split())[:140],
            }
            for r in candidates[:15]
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--report", default="reports/label_noise_report.json")
    args = ap.parse_args()

    res = analyze(_rows(Path(args.gold)))
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[label-noise] gold={res['total']} | 판례(신뢰출처 제외)={res['ruling_scored']} "
          f"| 노이즈(판례인데 비-S3)={res['noise_count']} | rate={res['noise_rate']:.1%}")
    print(f"[label-noise] noise_by_label={res['noise_by_label']}")
    print(f"[label-noise] noise_by_label_source={res['noise_by_label_source']}")
    print(f"[label-noise] report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

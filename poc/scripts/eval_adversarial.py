"""Adversarial regression gate — golden_100 적대 시나리오 상시 회귀 감시.

목적: 배포 모델이 *위험 방향*(gold 고비밀 TS/S1/S2 → 예측 S3)으로 회귀하지 않는지
상시 게이트화. golden_100은 단어 함정·출처 혼동 등 적대 케이스(짧은 시나리오)라
exact 정확도 자체는 낮으나(모델이 안전하게 상향 escalate), 핵심은 '비밀을 공개로
흘리는' 미탐이 baseline 대비 늘지 않는 것.

게이트: high-risk underclass(gold∈{TS,S1,S2} & pred=S3) 건수 <= --max-high-risk-miss.
baseline(step3 2026-06-05) = 4/65. 기본 임계 6(여유 2). 초과 시 회귀로 exit 1.

평가 모델 단일 진실원 = settings.classifier_model_dir(.env).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from eval_p1_model_gold import predict_direct  # noqa: E402  (src를 sys.path에 추가)
from lloydk.config import settings as _settings  # noqa: E402

HIGH = {"TS", "S1", "S2"}
_DEPLOYED = _settings.classifier_model_dir or "artifacts/classifier_p1_retrain_v4_step3/v-f9b5cedb"


def _load(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        r = json.loads(line)
        text = r.get("desc") or r.get("text") or ""
        gold = r.get("gold") or r.get("label") or r.get("expected_grade")
        if text and gold:
            rows.append({"text": text, "gold": gold, "trick": r.get("trick", "")})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=_DEPLOYED)
    ap.add_argument("--adversarial", default="datasets/adversarial/golden_100.jsonl")
    ap.add_argument("--report", default="reports/adversarial_gate")
    ap.add_argument("--max-high-risk-miss", type=int, default=6,
                    help="gold 고비밀→S3 미탐 허용 상한 (baseline step3=4). 초과 시 회귀 FAIL.")
    args = ap.parse_args()

    rows = _load(Path(args.adversarial))
    if not rows:
        print(f"[adversarial] {args.adversarial} 없음/비어있음 — gate skip", file=sys.stderr)
        return 2

    preds = predict_direct(Path(args.model_dir), [{"text": r["text"]} for r in rows])
    pl = [p["label"] for p in preds]

    n_high = sum(1 for r in rows if r["gold"] in HIGH)
    misses = [
        {"gold": r["gold"], "pred": p, "trick": r["trick"], "text": r["text"][:60]}
        for r, p in zip(rows, pl)
        if r["gold"] in HIGH and p == "S3"
    ]
    exact = sum(1 for r, p in zip(rows, pl) if r["gold"] == p)
    gate_pass = len(misses) <= args.max_high_risk_miss

    payload = {
        "model_dir": args.model_dir,
        "n": len(rows),
        "exact_accuracy": round(exact / len(rows), 4),
        "high_risk_total": n_high,
        "high_risk_underclass": len(misses),  # ← 게이트 지표 (위험 방향 미탐)
        "max_high_risk_miss": args.max_high_risk_miss,
        "gate": "PASS" if gate_pass else "FAIL",
        "pred_distribution": dict(Counter(pl)),
        "gold_distribution": dict(Counter(r["gold"] for r in rows)),
        "misses": misses,
    }
    md = [
        "# Adversarial Regression Gate (golden_100)",
        "",
        f"- model: `{args.model_dir}`  |  adversarial cases: {len(rows)}",
        f"- **gate: {payload['gate']}** — high-risk underclass {len(misses)}/{n_high} "
        f"(gold TS/S1/S2 → S3) <= {args.max_high_risk_miss}",
        f"- exact accuracy: {payload['exact_accuracy']:.0%} "
        "(적대 케이스라 낮음 — 모델이 안전하게 상향 escalate; 핵심은 위험 미탐 회귀 여부)",
        f"- pred dist: {payload['pred_distribution']}",
        "",
        "위험 방향(비밀→공개) 미탐이 baseline(=4)을 넘지 않는지 상시 회귀 감시. 초과 시 exit 1.",
    ]
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Build a CSV queue for external human review.

The queue is compatible with import_review_corrections.py. It prioritizes
high-risk underclassification examples from the P1 evaluation report, then fills
remaining slots from existing gold_real records.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

HIGH_RISK = {"TS", "S1", "S2"}

# 공개 판결문(판례) 마커 — 2개 이상이면 공개 사법문서로 보고 고위험 큐에서 제외.
# (공개 판례는 비공지성 실패로 S3가 정답 → 고위험 검증용으로 무의미)
RULING_MARKERS = [
    "【주 문】", "【주문】", "【이 유】", "【이유】", "대법원", "특허법원", "특허심판원",
    "선고", "심결", "상고", "항고심판", "원심판결", "거절사정", "권리범위확인",
    "등록무효", "거절결정", "등록취소", "소송대리인", "변리사",
]


def _is_ruling(row: dict) -> bool:
    if str(row.get("source", "")).startswith("판례"):
        return True
    text = row.get("text", "")
    return sum(1 for m in RULING_MARKERS if m in text) >= 2


def _confidence(row: dict) -> float:
    """검수 우선순위 보조키용 모델 confidence. 동일 priority 내에서 저신뢰를 먼저
    검수하도록 한다. 부재 시 1.0(=가장 덜 시급)으로 둬 기존 정렬을 보존(하위호환)."""
    for k in ("confidence", "score", "model_confidence"):
        v = row.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return 1.0


def _model_preds(report_paths: list[Path]) -> dict[str, str]:
    """리포트 errors에서 doc_id -> 모델 예측. 미수록 고위험 doc은 호출부에서 라벨=예측 가정."""
    preds: dict[str, str] = {}
    for p in report_paths:
        rep = _load_json(p)
        buckets = list(rep.get("priority_errors", {}).values()) + [rep.get("errors_sample", [])]
        for bucket in buckets:
            for e in bucket:
                d = str(e.get("doc_id", ""))
                if d and e.get("pred"):
                    preds[d] = str(e["pred"]).upper()
    return preds


def build_high_risk_queue(
    gold_by_id: dict[str, dict], model_preds: dict[str, str], limit: int
) -> list[dict]:
    """진짜 고위험(비판례) 문서 위주 검수 큐.

    우선순위: 0=모델이 S3로 과소분류(진짜 위험) > 1=llm 라벨(불확실) > 2=신뢰출처.
    이미 human_review인 건은 제외.
    """
    cands: list[tuple[int, float, dict]] = []
    for doc_id, src in gold_by_id.items():
        if src.get("label_source") == "human_review":
            continue
        label = str(src.get("label") or src.get("expected_grade") or "").upper()
        if label not in HIGH_RISK:
            continue
        if _is_ruling(src):
            continue
        model = model_preds.get(doc_id, label)  # 리포트에 없으면 모델이 라벨과 일치한 것
        ls = src.get("label_source", "")
        if model == "S3":
            pr = 0  # 고위험인데 모델이 S3 — 진짜 underclass, 최우선 검수
        elif ls in ("llm_judge_primary", "llm_judge_consensus"):
            pr = 1  # LLM 단독 라벨 — 가장 불확실
        else:
            pr = 2  # nkt/koipa/codex 등 신뢰출처 — 커버리지용
        cands.append((pr, _confidence(src), _row(doc_id, model, label, src)))
    # 동일 priority 내에서는 저신뢰(confidence 낮음) 우선 검수.
    cands.sort(key=lambda item: (item[0], item[1]))
    return [row for _, _, row in cands[:limit]]
FIELDNAMES = [
    "doc_id",
    "model_label",
    "human_label",
    "review_decision",
    "reason_code",
    "reason_text",
    "reviewer_id",
    "domain",
    "document_type",
    "text",
]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl_by_id(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        doc_id = row.get("doc_id")
        if doc_id:
            rows[str(doc_id)] = row
    return rows


def _row(doc_id: str, model_label: str, human_label: str, source: dict) -> dict:
    text = source.get("text") or source.get("text_preview") or ""
    return {
        "doc_id": doc_id,
        "model_label": model_label,
        "human_label": human_label,
        "review_decision": "",
        "reason_code": "",
        "reason_text": "",
        "reviewer_id": "",
        "domain": source.get("domain", ""),
        "document_type": source.get("document_type", ""),
        "text": text,
    }


def build_queue(report: dict, gold_by_id: dict[str, dict], limit: int) -> list[dict]:
    candidates: list[tuple[int, float, dict]] = []
    seen: set[str] = set()

    # 1) Full high-risk underclassification list. errors_sample is truncated
    # (e.g. 80 rows), so the genuinely dangerous TS/S1/S2 -> S3 cases must be
    # pulled from priority_errors, which carries the complete set.
    for err in report.get("priority_errors", {}).get("high_risk_to_s3", []):
        doc_id = str(err.get("doc_id", ""))
        if not doc_id or doc_id in seen:
            continue
        true_label = str(err.get("true", "")).upper()
        pred_label = str(err.get("pred", "")).upper()
        source = {**gold_by_id.get(doc_id, {}), **err}
        candidates.append((0, _confidence(source), _row(doc_id, pred_label, true_label, source)))
        seen.add(doc_id)

    for err in report.get("errors_sample", []):
        doc_id = str(err.get("doc_id", ""))
        if not doc_id or doc_id in seen:
            continue
        true_label = str(err.get("true", "")).upper()
        pred_label = str(err.get("pred", "")).upper()
        priority = 0 if true_label in HIGH_RISK and pred_label == "S3" else 1
        source = {**gold_by_id.get(doc_id, {}), **err}
        candidates.append((priority, _confidence(source), _row(doc_id, pred_label, true_label, source)))
        seen.add(doc_id)

    for doc_id, source in gold_by_id.items():
        if len(candidates) >= limit * 2:
            break
        if doc_id in seen or source.get("label_source") == "human_review":
            continue
        label = str(source.get("label") or source.get("expected_grade") or "").upper()
        if label not in {"TS", "S1", "S2", "S3"}:
            continue
        priority = 2 if label in HIGH_RISK else 3
        candidates.append((priority, _confidence(source), _row(doc_id, "", label, source)))
        seen.add(doc_id)

    # 동일 priority 내에서는 저신뢰(confidence 낮음) 우선 검수.
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [row for _, _, row in candidates[:limit]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="reports/p1_v3_llm_gold_direct.json")
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--out", default="datasets/corrections/human_review_queue.csv")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument(
        "--mode", choices=["errors", "high-risk"], default="errors",
        help="errors=리포트 오류 기반(기존) | high-risk=진짜 고위험(비판례) 위주",
    )
    ap.add_argument(
        "--model-reports",
        default="reports/p1_v3_public_gold_direct.json,reports/p1_v3_llm_gold_direct.json",
        help="high-risk 모드에서 모델 예측 출처(쉼표 구분)",
    )
    args = ap.parse_args()

    gold_by_id = _load_jsonl_by_id(Path(args.gold))
    if args.mode == "high-risk":
        preds = _model_preds([Path(p) for p in args.model_reports.split(",") if p.strip()])
        queue = build_high_risk_queue(gold_by_id, preds, args.limit)
    else:
        queue = build_queue(_load_json(Path(args.report)), gold_by_id, args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(queue)
    print(f"[human-review-queue] wrote {len(queue)} rows -> {out}")
    print("[human-review-queue] fill review_decision/reason_code/reviewer_id, then run:")
    print(f"  python scripts/import_review_corrections.py {out} --merge-gold --dry-run")
    return 0 if queue else 1


if __name__ == "__main__":
    raise SystemExit(main())

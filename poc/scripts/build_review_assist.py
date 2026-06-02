"""human_review 큐를 사람이 '처음부터 라벨링'하지 않고 '확인·승인'만 하도록
사전 분석을 채워주는 보조 도구.

핵심 판단 근거 (LABELING_GUIDE 등급 기준):
  비공지성(NON_PUBLICITY)이 핵심. 이미 공개된 문서는 본문이 무엇을 다루든 S3.
  이 큐의 문서는 대부분 공개된 법원/특허심판 판결문(판례)이라, 본문에
  '영업비밀·기밀·원가' 같은 단어가 등장해도 문서 자체는 공개 기록 → S3.
  pseudo-labeler가 그 단어를 보고 S1/S2로 올린 것이 오탐이며, 모델의 S3가 옳다.

이 스크립트는 라벨을 '확정'하지 않는다. 추천(human_label)·근거만 채우고
review_decision/reviewer_id는 비워 둔다. 사람이 확인 후 reviewer_id를
기입하고 import_review_corrections.py로 편입해야 human_review로 인정된다.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}  # 낮을수록 고위험


def _more_severe(a: str, b: str) -> str:
    """FNR-safe: 두 등급 중 더 높은(고위험) 쪽을 반환."""
    a, b = (a or "").upper(), (b or "").upper()
    if a not in GRADE_ORDER:
        return b
    if b not in GRADE_ORDER:
        return a
    return a if GRADE_ORDER[a] <= GRADE_ORDER[b] else b


# 공개 판결문/심결문 마커. 2개 이상이면 '공개된 사법 문서'로 간주.
RULING_MARKERS = [
    "【주 문】", "【주문】", "【이 유】", "【이유】", "【청구취지】",
    "대법원", "특허법원", "특허심판원", "고등법원", "지방법원",
    "선고", "심결", "상고", "항고심판", "원심판결", "원심심결",
    "거절사정", "권리범위확인", "등록무효", "거절결정", "등록취소",
    "【원고", "【피고", "【심판청구인", "소송대리인", "변리사", "변호사",
]

FIELDNAMES = [
    "doc_id", "model_label", "human_label", "review_decision",
    "reason_code", "reason_text", "reviewer_id", "domain", "document_type", "text",
]


def _load_queue(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _load_confidence(report_path: Path) -> dict[str, float]:
    """p1 리포트 priority_errors에서 doc_id -> 모델 confidence."""
    conf: dict[str, float] = {}
    if not report_path.exists():
        return conf
    rep = json.loads(report_path.read_text(encoding="utf-8"))
    for bucket in rep.get("priority_errors", {}).values():
        for e in bucket:
            if e.get("doc_id") and e.get("confidence") is not None:
                conf[str(e["doc_id"])] = float(e["confidence"])
    return conf


def _ruling_hits(text: str) -> list[str]:
    return [m for m in RULING_MARKERS if m in text]


def assess(row: dict, conf: dict[str, float]) -> dict:
    text = row.get("text", "")
    model_label = (row.get("model_label") or "").upper()
    pseudo = (row.get("human_label") or "").upper()  # 큐의 human_label = pseudo true
    hits = _ruling_hits(text)
    is_ruling = len(hits) >= 2

    if is_ruling:
        # 공개 판결문: 비공지성 실패 → S3. 모델이 S3면 일치, pseudo가 더 높으면 pseudo가 오탐.
        recommend = "S3"
        reason_code = "public_court_ruling"
        reason_text = (
            f"공개된 사법 문서(마커 {len(hits)}개: {', '.join(hits[:4])}). "
            f"비공지성 실패 → S3. pseudo={pseudo}는 본문 키워드 오탐 추정, model={model_label}."
        )
        # 확신: 마커 충분 + 모델도 S3면 auto-confirm 후보
        triage = "AUTO-CONFIRM" if (len(hits) >= 4 and model_label == "S3") else "QUICK-CHECK"
    else:
        # 판결문 아님 → 자동 판단 보류, FNR-safe로 더 높은 등급 쪽을 추천
        recommend = _more_severe(model_label, pseudo)
        reason_code = "needs_review"
        reason_text = (
            f"사법 문서 마커 부족({len(hits)}개) → 자동 판단 보류. "
            f"model={model_label}/pseudo={pseudo} 중 FNR-safe로 {recommend} 추천하나 사람 확인 필요."
        )
        triage = "REVIEW"

    return {
        "recommend": recommend,
        "reason_code": reason_code,
        "reason_text": reason_text,
        "triage": triage,
        "ruling_hits": len(hits),
        "confidence": conf.get(str(row.get("doc_id")), None),
    }


def build(queue: list[dict], conf: dict[str, float]) -> list[dict]:
    out = []
    for row in queue:
        a = assess(row, conf)
        out.append({**row, "_assess": a})
    return out


def write_prefilled_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            a = r["_assess"]
            w.writerow({
                "doc_id": r.get("doc_id", ""),
                "model_label": r.get("model_label", ""),
                "human_label": a["recommend"],        # AI 추천 (사람이 확인/수정)
                "review_decision": "",                 # 사람이 채움
                "reason_code": a["reason_code"],
                "reason_text": a["reason_text"],
                "reviewer_id": "",                     # 사람이 사인오프 시 기입
                "domain": r.get("domain", ""),
                "document_type": r.get("document_type", ""),
                "text": r.get("text", ""),
            })


def write_worksheet(rows: list[dict], path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    triage_counts: dict[str, int] = {}
    rec_counts: dict[str, int] = {}
    for r in rows:
        a = r["_assess"]
        triage_counts[a["triage"]] = triage_counts.get(a["triage"], 0) + 1
        rec_counts[a["recommend"]] = rec_counts.get(a["recommend"], 0) + 1

    lines = [
        "# Human Review Worksheet (AI pre-analysis)",
        "",
        "AI가 등급 기준(LABELING_GUIDE)으로 사전 분석한 추천이다. **확정 라벨이 아니다.**",
        "각 항목을 확인하고, 동의하면 reviewer_id를 채워 prefilled CSV를 import하라.",
        "공개 판결문은 비공지성 실패로 S3가 원칙(본문 키워드는 오탐 유발).",
        "",
        f"- 추천 분포: {rec_counts}",
        f"- triage: {triage_counts}",
        "  - AUTO-CONFIRM: 마커 충분 + 모델 S3 일치 → 빠른 일괄 확인 가능",
        "  - QUICK-CHECK: 공개 판결문이나 모델과 불일치 → 한 번 눈으로 확인",
        "  - REVIEW: 사법 문서 마커 부족 → 사람이 직접 판단",
        "",
        "| # | doc_id | model | pseudo | 추천 | triage | 마커 | conf |",
        "|---|---|:--:|:--:|:--:|---|:--:|:--:|",
    ]
    # triage 우선순위로 정렬: REVIEW > QUICK-CHECK > AUTO-CONFIRM
    order = {"REVIEW": 0, "QUICK-CHECK": 1, "AUTO-CONFIRM": 2}
    rows_sorted = sorted(rows, key=lambda r: order.get(r["_assess"]["triage"], 9))
    for i, r in enumerate(rows_sorted, 1):
        a = r["_assess"]
        c = f"{a['confidence']:.2f}" if a["confidence"] is not None else "-"
        lines.append(
            f"| {i} | `{r.get('doc_id','')[:12]}` | {r.get('model_label','')} | "
            f"{r.get('human_label','')} | **{a['recommend']}** | {a['triage']} | {a['ruling_hits']} | {c} |"
        )

    lines += ["", "## 항목별 근거 + 본문 발췌", ""]
    for i, r in enumerate(rows_sorted, 1):
        a = r["_assess"]
        snippet = re.sub(r"\s+", " ", r.get("text", ""))[:300]
        lines += [
            f"### {i}. `{r.get('doc_id','')[:12]}` — 추천 **{a['recommend']}** ({a['triage']})",
            f"- model={r.get('model_label','')}, pseudo={r.get('human_label','')}, 마커={a['ruling_hits']}",
            f"- 근거: {a['reason_text']}",
            f"- 발췌: {snippet}…",
            "",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"triage": triage_counts, "recommend": rec_counts, "n": len(rows)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", default="datasets/corrections/human_review_queue.csv")
    ap.add_argument("--report", default="reports/p1_v3_llm_gold_direct.json")
    ap.add_argument("--out-csv", default="datasets/corrections/human_review_queue.prefilled.csv")
    ap.add_argument("--out-md", default="datasets/corrections/human_review_worksheet.md")
    args = ap.parse_args()

    queue = _load_queue(Path(args.queue))
    if not queue:
        print(f"[review-assist] empty queue: {args.queue}")
        return 1
    conf = _load_confidence(Path(args.report))
    rows = build(queue, conf)

    write_prefilled_csv(rows, Path(args.out_csv))
    stats = write_worksheet(rows, Path(args.out_md))

    print(f"[review-assist] {stats['n']} rows | recommend={stats['recommend']} | triage={stats['triage']}")
    print(f"[review-assist] worksheet -> {args.out_md}")
    print(f"[review-assist] prefilled -> {args.out_csv}")
    print("[review-assist] 확인 후 reviewer_id 기입 → import_review_corrections.py ... --merge-gold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

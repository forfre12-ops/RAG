"""Evaluate a trained P1 classifier on gold_real with direct and FNR-safe modes."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

LABELS = ["TS", "S1", "S2", "S3"]
GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}


def load_jsonl(path: Path, label_sources: set[str] | None = None) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        label = row.get("label") or row.get("expected_grade")
        if label not in LABELS:
            continue
        if label_sources and row.get("label_source") not in label_sources:
            continue
        rows.append({**row, "label": label})
    return rows


def compute_metrics(y_true: list[str], y_pred: list[str]) -> dict:
    cm = {t: {p: 0 for p in LABELS} for t in LABELS}
    for t, p in zip(y_true, y_pred):
        if t in cm and p in cm[t]:
            cm[t][p] += 1

    per_class = {}
    for lbl in LABELS:
        tp = cm[lbl][lbl]
        fp = sum(cm[t][lbl] for t in LABELS if t != lbl)
        fn = sum(cm[lbl][p] for p in LABELS if p != lbl)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[lbl] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "support": sum(cm[lbl].values()),
        }

    high_total = sum(1 for t in y_true if GRADE_ORDER[t] <= 2)
    high_under = sum(
        1
        for t, p in zip(y_true, y_pred)
        if GRADE_ORDER[t] <= 2 and GRADE_ORDER[p] > GRADE_ORDER[t]
    )
    down_to_s3 = sum(1 for t, p in zip(y_true, y_pred) if t in {"TS", "S1", "S2"} and p == "S3")
    n = len(y_true)
    return {
        "n": n,
        "accuracy": round(sum(t == p for t, p in zip(y_true, y_pred)) / n, 4) if n else 0.0,
        "f1_macro": round(sum(c["f1"] for c in per_class.values()) / len(LABELS), 4),
        "fnr_underclass": round(high_under / high_total, 4) if high_total else 0.0,
        "high_risk_to_s3": down_to_s3,
        "per_class": per_class,
        "confusion_matrix": cm,
    }


def predict_direct(model_dir: Path, rows: list[dict], batch_size: int = 16) -> list[dict]:
    import torch
    import torch.nn.functional as F
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir)).to(device)
    model.eval()
    id2label = {int(k): v for k, v in model.config.id2label.items()}
    preds: list[dict] = []
    texts = [r["text"] for r in rows]
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            enc = tok(batch, truncation=True, max_length=512, padding=True, return_tensors="pt").to(device)
            probs = F.softmax(model(**enc).logits, dim=-1).cpu()
            for row_probs in probs:
                idx = int(row_probs.argmax().item())
                scores = {id2label[i]: float(row_probs[i]) for i in range(len(id2label))}
                preds.append({"label": id2label[idx], "confidence": float(row_probs[idx]), "scores": scores})
    return preds


def predict_api_like(model_dir: Path, rows: list[dict]) -> list[dict]:
    from lloydk.modules.m5_inference.pipeline import InferencePipeline

    pipe = InferencePipeline(model_dir=model_dir)
    preds = []
    for row in rows:
        # source_prior 게이트가 동작하려면 gold의 source/source_type을 metadata로 전달.
        # (gold는 top-level `source`를 갖지만 pipeline은 metadata.source_type/source를 참조 —
        #  미전달 시 source_prior가 영구 무발동이었음.)
        md = {}
        if row.get("source"):
            md["source"] = row["source"]
        if row.get("source_type"):
            md["source_type"] = row["source_type"]
        out = pipe.run(row["text"], return_evidence=False, metadata=md or None)
        label = out.label.value if hasattr(out.label, "value") else str(out.label)
        preds.append(
            {
                "label": label,
                "confidence": out.confidence,
                "scores": out.scores,
                "warnings": out.warnings,
            }
        )
    return preds


def write_report(payload: dict, out: Path) -> None:
    m = payload["metrics"]
    md = [
        f"# P1 Model Gold Evaluation ({payload['mode']})",
        "",
        f"- model_dir: `{payload['model_dir']}`",
        f"- gold: `{payload['gold']}`",
        f"- label_sources: `{','.join(payload['label_sources']) if payload['label_sources'] else 'ALL'}`",
        f"- n: {m['n']}",
        f"- F1 macro: {m['f1_macro']:.3f}",
        f"- FNR underclass: {m['fnr_underclass']:.3f}",
        f"- TS/S1/S2 -> S3: {m['high_risk_to_s3']}",
        "",
        "| Grade | P | R | F1 | N |",
        "|---|---:|---:|---:|---:|",
    ]
    for g in LABELS:
        c = m["per_class"][g]
        md.append(f"| {g} | {c['precision']:.3f} | {c['recall']:.3f} | {c['f1']:.3f} | {c['support']} |")
    md += ["", "## Confusion Matrix", "", "| truth \\ pred | TS | S1 | S2 | S3 |", "|---|---:|---:|---:|---:|"]
    for g in LABELS:
        row = m["confusion_matrix"][g]
        md.append(f"| {g} | {row['TS']} | {row['S1']} | {row['S2']} | {row['S3']} |")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def summarize_errors(rows: list[dict], y_true: list[str], y_pred: list[str], preds: list[dict], limit: int) -> dict:
    pattern_counts: Counter = Counter()
    high_risk_to_s3: list[dict] = []
    s3_overclassified: list[dict] = []
    for r, t, p, pred in zip(rows, y_true, y_pred, preds):
        if t == p:
            continue
        pattern_counts[f"{t}->{p}"] += 1
        item = {
            "doc_id": r.get("doc_id"),
            "label_source": r.get("label_source"),
            "true": t,
            "pred": p,
            "confidence": pred.get("confidence"),
            "text_preview": r.get("text", "")[:400],
            "warnings": pred.get("warnings", []),
        }
        if t in {"TS", "S1", "S2"} and p == "S3" and len(high_risk_to_s3) < limit:
            high_risk_to_s3.append(item)
        if t == "S3" and p in {"TS", "S1", "S2"} and len(s3_overclassified) < limit:
            s3_overclassified.append(item)
    return {
        "pattern_counts": dict(pattern_counts.most_common()),
        "priority_errors": {
            "high_risk_to_s3": high_risk_to_s3,
            "s3_overclassified": s3_overclassified,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--label-source-filter", default="")
    ap.add_argument("--tier", choices=["all", "eval", "locked"], default="all",
                    help="평가 tier: all=전체(레거시) / eval=locked_gold_eval(없으면 legal_floor interim) / locked=locked만")
    ap.add_argument("--mode", choices=["direct", "api-like"], default="direct")
    ap.add_argument("--report", default="reports/p1_model_gold_eval.md")
    ap.add_argument("--examples", type=int, default=30)
    args = ap.parse_args()

    label_sources = {s.strip() for s in args.label_source_filter.split(",") if s.strip()}
    rows = load_jsonl(Path(args.gold), label_sources or None)
    eval_tier = "all"
    if args.tier in ("eval", "locked"):
        from lloydk.golden_tiers import eval_records
        rows, eval_tier = eval_records(rows, allow_floor_fallback=(args.tier == "eval"))
        if not rows:
            print(
                f"[ERROR] --tier {args.tier}: 레코드 0건 "
                f"(locked_gold_eval 비어있음 — 9월 실데이터+사람서명 전).",
                file=sys.stderr,
            )
            return 2
        if eval_tier == "legal_floor":
            print(
                "[WARN] locked_gold_eval 없음 → legal_floor(시나리오/법적) interim. '템플릿 recall'이지 "
                "일반화 진실 아님 — 릴리스 차단 floor로만, 헤드라인 F1로 보고 금지.",
                file=sys.stderr,
            )
        print(f"[tier] eval tier={eval_tier}, n={len(rows)}")
    preds = (
        predict_api_like(Path(args.model_dir), rows)
        if args.mode == "api-like"
        else predict_direct(Path(args.model_dir), rows)
    )
    y_true = [r["label"] for r in rows]
    y_pred = [p["label"] for p in preds]
    errors = [
        {
            "doc_id": r.get("doc_id"),
            "label_source": r.get("label_source"),
            "true": t,
            "pred": p,
            "text_preview": r.get("text", "")[:240],
            "warnings": pred.get("warnings", []),
        }
        for r, t, p, pred in zip(rows, y_true, y_pred, preds)
        if t != p
    ]
    payload = {
        "model_dir": args.model_dir,
        "gold": args.gold,
        "mode": args.mode,
        "label_sources": sorted(label_sources),
        "eval_tier": eval_tier,
        "metrics": compute_metrics(y_true, y_pred),
        "prediction_distribution": dict(Counter(y_pred)),
        "error_count": len(errors),
        "errors_sample": errors[: args.examples],
    }
    payload.update(summarize_errors(rows, y_true, y_pred, preds, args.examples))
    write_report(payload, Path(args.report))
    print(json.dumps({k: payload[k] for k in ("mode", "metrics", "prediction_distribution", "error_count")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from analyze_label_noise import _is_ruling  # noqa: E402
from lloydk.golden_tiers import tier_of  # noqa: E402

LABELS = ("TS", "S1", "S2", "S3")
LLM_SOURCES = {"llm_judge_primary", "llm_judge_consensus"}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def latest_build(pattern: str) -> Path:
    files = sorted(Path("datasets/gold_real/builds").glob(pattern))
    if not files:
        raise FileNotFoundError(f"no files matching datasets/gold_real/builds/{pattern}")
    return files[-1]


def counts(rows: list[dict], key: str) -> dict[str, int]:
    return dict(Counter(str(r.get(key) or "") for r in rows))


def label_counts(rows: list[dict]) -> dict[str, int]:
    base = Counter(r.get("label") for r in rows)
    return {label: base.get(label, 0) for label in LABELS}


def source_label_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        out[str(row.get("label_source") or "")][str(row.get("label") or "")] += 1
    return {src: dict(counter) for src, counter in sorted(out.items())}


def norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def train_overlap(rows: list[dict], train_rows: list[dict]) -> dict[str, float | int]:
    train_texts = {norm_text(r.get("text", "")) for r in train_rows if r.get("text")}
    overlap = sum(1 for r in rows if norm_text(r.get("text", "")) in train_texts)
    return {
        "count": overlap,
        "total": len(rows),
        "rate": round(overlap / len(rows), 4) if rows else 0.0,
    }


def annotate(row: dict, nohuman_tier: str, reason: str) -> dict:
    out = dict(row)
    out["nohuman_tier"] = nohuman_tier
    out["nohuman_reason"] = reason
    out["truth_warning"] = (
        "auto_proxy_only_not_locked_gold_eval"
        if nohuman_tier == "proxy_eval"
        else "not_eval_truth"
    )
    return out


def build(original: list[dict], regate_gold: list[dict], regate_review: list[dict]) -> dict:
    regate_gold_ids = {str(r.get("doc_id")) for r in regate_gold}
    review_by_id = {str(r.get("doc_id")): r for r in regate_review}

    original_by_id = {str(r.get("doc_id")): r for r in original}
    proxy_eval: list[dict] = []
    quarantine: list[dict] = []
    silver_train_only: list[dict] = []

    quarantine_ids: set[str] = set()
    quarantine_reasons: dict[str, list[str]] = defaultdict(list)
    for row in original:
        doc_id = str(row.get("doc_id"))
        src = row.get("label_source")
        label = str(row.get("label") or "").upper()
        if src in LLM_SOURCES and _is_ruling(row) and label != "S3":
            quarantine_ids.add(doc_id)
            quarantine_reasons[doc_id].append("public_or_ruling_labeled_high_risk_by_llm")

    for row in regate_review:
        doc_id = str(row.get("doc_id"))
        status = str(row.get("status") or row.get("review_status") or "")
        if "ts_downgrade" in status:
            quarantine_ids.add(doc_id)
            quarantine_reasons[doc_id].append("ts_downgrade_suspect")

    for row in regate_gold:
        doc_id = str(row.get("doc_id"))
        src = row.get("label_source")
        tier = tier_of(row)
        if tier == "legal_floor":
            reason = f"legal_floor:{src}"
        elif tier == "gold_candidate":
            reason = "rule_llm_agreement_with_evidence"
        else:
            reason = f"regate_gold:{tier}"
        proxy_eval.append(annotate(row, "proxy_eval", reason))

    for doc_id, row in original_by_id.items():
        if doc_id in regate_gold_ids:
            continue
        if doc_id in quarantine_ids:
            reason = "+".join(sorted(set(quarantine_reasons[doc_id])))
            quarantine.append(annotate(row, "quarantine", reason))
            continue
        review = review_by_id.get(doc_id)
        if review:
            status = str(review.get("status") or "needs_review")
            reason = f"regate_demoted:{status}"
        else:
            reason = "not_proxy_eval"
        silver_train_only.append(annotate(row, "silver_train_only", reason))

    manifest = {
        "input_total": len(original),
        "proxy_eval": {
            "count": len(proxy_eval),
            "labels": label_counts(proxy_eval),
            "label_source": counts(proxy_eval, "label_source"),
            "source_label": source_label_counts(proxy_eval),
        },
        "silver_train_only": {
            "count": len(silver_train_only),
            "labels": label_counts(silver_train_only),
            "label_source": counts(silver_train_only, "label_source"),
            "reasons": counts(silver_train_only, "nohuman_reason"),
        },
        "quarantine": {
            "count": len(quarantine),
            "labels": label_counts(quarantine),
            "label_source": counts(quarantine, "label_source"),
            "reasons": counts(quarantine, "nohuman_reason"),
        },
        "locked_gold_eval_count": sum(1 for r in original if tier_of(r) == "locked_gold_eval"),
        "warning": (
            "No human-review locked gold is created here. proxy_eval is only an "
            "automatic, no-human evaluation proxy."
        ),
    }
    return {
        "proxy_eval": proxy_eval,
        "silver_train_only": silver_train_only,
        "quarantine": quarantine,
        "manifest": manifest,
    }


def render_report(manifest: dict, regate_gold: Path, regate_review: Path) -> str:
    px = manifest["proxy_eval"]
    sv = manifest["silver_train_only"]
    qt = manifest["quarantine"]
    return "\n".join(
        [
            "# No-Human Proxy Gold Report",
            "",
            "## Verdict",
            "",
            "- locked_gold_eval: 0",
            f"- proxy_eval: {px['count']} / {manifest['input_total']}",
            f"- silver_train_only: {sv['count']}",
            f"- quarantine: {qt['count']}",
            "- Use proxy_eval for smoke/regression only. Do not call it true gold.",
            "",
            "## Inputs",
            "",
            f"- regate_gold: `{regate_gold}`",
            f"- regate_review: `{regate_review}`",
            "",
            "## Proxy Eval Label Counts",
            "",
            json.dumps(px["labels"], ensure_ascii=False, indent=2),
            "",
            "## Proxy Eval Sources",
            "",
            json.dumps(px["label_source"], ensure_ascii=False, indent=2),
            "",
            "## Quarantine Reasons",
            "",
            json.dumps(qt["reasons"], ensure_ascii=False, indent=2),
            "",
            "## Train Text Overlap",
            "",
            json.dumps(manifest.get("train_text_overlap", {}), ensure_ascii=False, indent=2),
            "",
            "## Operational Rule",
            "",
            "1. Evaluate only on `proxy_eval_nohuman.jsonl` when human review is unavailable.",
            "2. Train may use `silver_train_only_nohuman.jsonl`, but do not report it as accuracy.",
            "3. Exclude `quarantine_nohuman.jsonl` by default until a human or customer-side authority resolves it.",
            "4. If proxy_eval has train text overlap, report scores as regression smoke only, not honest accuracy.",
            "5. September customer documents should run in shadow mode because proxy_eval is distribution-limited.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    parser.add_argument("--regate-gold", default="")
    parser.add_argument("--regate-review", default="")
    parser.add_argument("--train", default="datasets/gold_real/train_subset.jsonl")
    parser.add_argument("--out-dir", default="datasets/gold_real/nohuman_proxy")
    args = parser.parse_args()

    gold_path = Path(args.gold)
    regate_gold = Path(args.regate_gold) if args.regate_gold else latest_build("regate_gold_*.jsonl")
    regate_review = Path(args.regate_review) if args.regate_review else latest_build("regate_review_*.jsonl")
    out_dir = Path(args.out_dir)

    result = build(load_jsonl(gold_path), load_jsonl(regate_gold), load_jsonl(regate_review))
    train_path = Path(args.train)
    if train_path.exists():
        train_rows = load_jsonl(train_path)
        result["manifest"]["train_text_overlap"] = {
            "train": str(train_path),
            "proxy_eval": train_overlap(result["proxy_eval"], train_rows),
            "silver_train_only": train_overlap(result["silver_train_only"], train_rows),
            "quarantine": train_overlap(result["quarantine"], train_rows),
        }
    else:
        result["manifest"]["train_text_overlap"] = {"train": str(train_path), "error": "not_found"}
    write_jsonl(out_dir / "proxy_eval_nohuman.jsonl", result["proxy_eval"])
    write_jsonl(out_dir / "silver_train_only_nohuman.jsonl", result["silver_train_only"])
    write_jsonl(out_dir / "quarantine_nohuman.jsonl", result["quarantine"])
    (out_dir / "manifest.json").write_text(
        json.dumps(result["manifest"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "README.md").write_text(
        render_report(result["manifest"], regate_gold, regate_review),
        encoding="utf-8",
    )

    manifest = result["manifest"]
    print(f"proxy_eval={manifest['proxy_eval']['count']}")
    print(f"silver_train_only={manifest['silver_train_only']['count']}")
    print(f"quarantine={manifest['quarantine']['count']}")
    print(f"out_dir={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

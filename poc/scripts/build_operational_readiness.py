"""Build an operational readiness summary from existing evaluation artifacts.

The script is intentionally offline: it reads JSON reports and dataset files that
already exist in the workspace, then writes a compact Markdown/JSON gate report.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Gate:
    name: str
    status: str
    detail: str


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _count_jsonl(path: Path) -> tuple[int, Counter, Counter]:
    grade_counts: Counter = Counter()
    source_counts: Counter = Counter()
    if not path.exists():
        return 0, grade_counts, source_counts
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        n += 1
        grade = row.get("label") or row.get("expected_grade")
        if grade:
            grade_counts[str(grade)] += 1
        source = row.get("label_source") or row.get("source")
        if source:
            source_counts[str(source)] += 1
    return n, grade_counts, source_counts


def _p1_gate(public_report: dict, llm_report: dict) -> tuple[Gate, dict]:
    public_m = public_report.get("metrics", {})
    llm_m = llm_report.get("metrics", {})
    public_pass = (
        public_m.get("f1_macro", 0) >= 0.75
        and public_m.get("fnr_underclass", 1) <= 0.05
        and public_m.get("high_risk_to_s3", 999) == 0
    )
    status = "PASS" if public_pass else "FAIL"
    detail = (
        f"public/case/nkt F1={public_m.get('f1_macro', 0):.3f}, "
        f"FNR={public_m.get('fnr_underclass', 0):.3f}, "
        f"high-risk->S3={public_m.get('high_risk_to_s3', 'N/A')}; "
        f"llm pseudo F1={llm_m.get('f1_macro', 0):.3f}, "
        f"high-risk->S3={llm_m.get('high_risk_to_s3', 'N/A')}"
    )
    return Gate("P1 classifier", status, detail), {"public": public_m, "llm_pseudo": llm_m}


def _p2_gate(p2_report: dict) -> tuple[Gate, dict]:
    best = p2_report.get("best_config", {})
    metrics = best.get("retrieval_metrics", {})
    recall = metrics.get("recall_at_k", best.get("recall_at_k", 0))
    latency = best.get("latency_ms_p50", 999999)
    status = "PASS" if recall >= 0.80 and latency <= 200 else "FAIL"
    detail = (
        f"{best.get('label', 'N/A')}: Recall@5={recall:.3f}, "
        f"MRR={metrics.get('mrr', 0):.3f}, nDCG@5={metrics.get('ndcg_at_k', 0):.3f}, "
        f"p50={latency:.0f}ms"
    )
    return Gate("P2 retrieval", status, detail), {"best_config": best}


def _data_gates(gold_path: Path, retrieval_gold_path: Path, min_human_review: int) -> tuple[list[Gate], dict]:
    gold_n, grade_counts, source_counts = _count_jsonl(gold_path)
    retrieval_n, _, retrieval_sources = _count_jsonl(retrieval_gold_path)
    human_review = source_counts.get("human_review", 0)
    gates = [
        Gate(
            "classification gold size",
            "PASS" if gold_n >= 700 else "FAIL",
            f"{gold_n} records, grades={dict(sorted(grade_counts.items()))}",
        ),
        Gate(
            "human review gold",
            "BLOCKED" if human_review < min_human_review else "PASS",
            f"human_review={human_review}/{min_human_review}; external reviewed samples still required",
        ),
        Gate(
            "retrieval gold size",
            "PASS" if retrieval_n >= 80 else "FAIL",
            f"{retrieval_n} doc-id queries, sources={dict(sorted(retrieval_sources.items()))}",
        ),
    ]
    return gates, {
        "classification_gold": {
            "path": str(gold_path),
            "records": gold_n,
            "grade_distribution": dict(sorted(grade_counts.items())),
            "source_distribution": dict(sorted(source_counts.items())),
        },
        "retrieval_gold": {
            "path": str(retrieval_gold_path),
            "records": retrieval_n,
            "source_distribution": dict(sorted(retrieval_sources.items())),
        },
    }


def _overall(gates: list[Gate]) -> str:
    if any(g.status == "FAIL" for g in gates):
        return "FAIL"
    if any(g.status == "BLOCKED" for g in gates):
        return "CONDITIONALLY_READY"
    return "PASS"


def _write_md(payload: dict, out: Path) -> None:
    gates = payload["gates"]
    lines = [
        "# Operational Readiness",
        "",
        f"- Verdict: **{payload['verdict']}**",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Current model: `{payload['current_model']}`",
        f"- Retrieval config: `{payload['retrieval_config']}`",
        "",
        "## Gates",
        "",
        "| Gate | Status | Detail |",
        "|---|:---:|---|",
    ]
    for g in gates:
        lines.append(f"| {g['name']} | {g['status']} | {g['detail']} |")

    lines += [
        "",
        "## Required Next Actions",
        "",
    ]
    for item in payload["next_actions"]:
        lines.append(f"- {item}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--p1-public", default="reports/p1_v3_public_gold_direct.json")
    ap.add_argument("--p1-llm", default="reports/p1_v3_llm_gold_direct.json")
    ap.add_argument("--p2", default="reports/p2_gold_kure_es_hybrid_v3.json")
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--retrieval-gold", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_retrain_v3/v-3443785f")
    ap.add_argument("--out", default="reports/operational_readiness.md")
    ap.add_argument("--min-human-review", type=int, default=40)
    args = ap.parse_args()

    p1_gate, p1_payload = _p1_gate(_load_json(Path(args.p1_public)), _load_json(Path(args.p1_llm)))
    p2_gate, p2_payload = _p2_gate(_load_json(Path(args.p2)))
    data_gates, data_payload = _data_gates(Path(args.gold), Path(args.retrieval_gold), args.min_human_review)

    gate_objects = [p1_gate, p2_gate, *data_gates]
    next_actions = [
        f"Collect at least {args.min_human_review} human_review gold samples; this is the only blocked readiness gate.",
        "Review LLM pseudo-gold S2->S3 cases and either relabel or add boundary examples.",
        "Promote CLASSIFIER_MODEL_DIR to the P1 v3 model after release approval.",
        "Run p2-full-gold after every ES reindex or embedding-model change.",
    ]
    payload = {
        "generated_at": "2026-06-02",
        "verdict": _overall(gate_objects),
        "current_model": args.model_dir,
        "retrieval_config": "KURE-v1 + Elasticsearch hybrid + chunk=1200/overlap=100",
        "release_gate_policy": {"min_human_review": args.min_human_review},
        "gates": [g.__dict__ for g in gate_objects],
        "p1": p1_payload,
        "p2": p2_payload,
        "data": data_payload,
        "next_actions": next_actions,
    }

    out = Path(args.out)
    _write_md(payload, out)
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": payload["verdict"], "gates": payload["gates"]}, ensure_ascii=False, indent=2))
    return 0 if payload["verdict"] in {"PASS", "CONDITIONALLY_READY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

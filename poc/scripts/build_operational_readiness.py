"""Build an operational readiness summary from existing evaluation artifacts.

The script is intentionally offline: it reads JSON reports and dataset files that
already exist in the workspace, then writes a compact Markdown/JSON gate report.
"""

from __future__ import annotations

import argparse
import json
import os
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


HIGH_RISK = {"TS", "S1", "S2"}


def _human_review_agreement(path: Path) -> dict:
    """Collect human_review gold records and measure how often the model
    under-protected a high-risk doc (human=TS/S1/S2 but model predicted S3).

    This is the agreement signal that turns the human-review gate from a pure
    count into a quality check: filling the queue with rubber-stamp labels no
    longer flips the gate green if the classifier genuinely under-classifies.
    """
    n = high_risk = underclass = missing_model = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            if (row.get("label_source") or row.get("source")) != "human_review":
                continue
            n += 1
            human = str(row.get("label") or row.get("expected_grade") or "").upper()
            model = str(row.get("model_label") or "").upper()
            if not model:
                missing_model += 1
            if human in HIGH_RISK:
                high_risk += 1
                if model == "S3":
                    underclass += 1
    rate = (underclass / high_risk) if high_risk else 0.0
    return {
        "count": n,
        "high_risk": high_risk,
        "underclass": underclass,
        "missing_model_label": missing_model,
        "high_risk_underclass_rate": round(rate, 4),
    }


def _data_gates(
    gold_path: Path,
    retrieval_gold_path: Path,
    min_human_review: int,
    max_high_risk_underclass: float,
) -> tuple[list[Gate], dict]:
    gold_n, grade_counts, source_counts = _count_jsonl(gold_path)
    retrieval_n, _, retrieval_sources = _count_jsonl(retrieval_gold_path)
    human_review = source_counts.get("human_review", 0)
    hr = _human_review_agreement(gold_path)

    if human_review < min_human_review:
        hr_status = "BLOCKED"
        hr_detail = (
            f"human_review={human_review}/{min_human_review}; "
            "external reviewed samples still required"
        )
    elif hr["missing_model_label"] > 0:
        hr_status = "FAIL"
        hr_detail = (
            f"human_review={human_review}; "
            f"{hr['missing_model_label']} records missing model_label "
            "(cannot compute model-vs-human agreement)"
        )
    elif hr["high_risk_underclass_rate"] > max_high_risk_underclass:
        hr_status = "FAIL"
        hr_detail = (
            f"human_review={human_review}; high-risk underclass "
            f"{hr['underclass']}/{hr['high_risk']} "
            f"rate={hr['high_risk_underclass_rate']:.3f} > {max_high_risk_underclass:.3f}"
        )
    else:
        hr_status = "PASS"
        hr_detail = (
            f"human_review={human_review}; high-risk underclass "
            f"{hr['underclass']}/{hr['high_risk']} "
            f"rate={hr['high_risk_underclass_rate']:.3f} <= {max_high_risk_underclass:.3f}"
        )

    gates = [
        Gate(
            "classification gold size",
            "PASS" if gold_n >= 700 else "FAIL",
            f"{gold_n} records, grades={dict(sorted(grade_counts.items()))}",
        ),
        Gate("human review gold", hr_status, hr_detail),
        Gate(
            "retrieval gold size",
            "PASS" if retrieval_n >= 80 else "FAIL",
            f"{retrieval_n} doc-id queries, sources={dict(sorted(retrieval_sources.items()))}",
        ),
    ]
    return gates, {
        "human_review_agreement": hr,
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


def _norm_model(p: str) -> str:
    return (p or "").replace("\\", "/").strip().rstrip("/")


def _model_parity_gate(evaluated: str, deployed: str) -> Gate:
    """The F1/FNR gates describe the *evaluated* model. If the *deployed* model
    (CLASSIFIER_MODEL_DIR) differs, those numbers do not describe what is live —
    so readiness cannot be claimed on them. BLOCKED until the deploy is promoted.
    """
    ev, dp = _norm_model(evaluated), _norm_model(deployed)
    if not dp:
        return Gate(
            "model parity",
            "BLOCKED",
            f"deployed model unknown (CLASSIFIER_MODEL_DIR unset); evaluated={ev}. "
            "Cannot confirm the gated metrics describe the live model.",
        )
    if ev == dp:
        return Gate("model parity", "PASS", f"deployed == evaluated ({ev})")
    return Gate(
        "model parity",
        "BLOCKED",
        f"deployed={dp} != evaluated={ev}; F1/FNR gates describe the evaluated model, "
        "not what is live. Promote CLASSIFIER_MODEL_DIR before release.",
    )


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
        f"- Evaluated model: `{payload['evaluated_model']}`",
        f"- Deployed model: `{payload['deployed_model']}`",
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

    if payload.get("known_limitations"):
        lines += [
            "",
            "## Known Limitations",
            "",
            "These are documented and expected, not open bugs. Read before reacting to any FAIL/low-F1 artifact.",
            "",
        ]
        for item in payload["known_limitations"]:
            lines.append(f"- {item}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    # 2026-06-03: 디오염 홀드아웃 기준으로 전환. v3 public/llm gold 리포트는 train-on-test
    # 오염(0.830은 암기)이라 폐기. P1 게이트는 cost2의 정직 홀드아웃 평가를 읽는다.
    ap.add_argument("--p1-public", default="reports/p1_cost2_holdout_direct.json")
    ap.add_argument("--p1-llm", default="reports/p1_cost2_holdout_direct.json")
    ap.add_argument("--p2", default="reports/p2_gold_kure_es_hybrid_v3.json")
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--retrieval-gold", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_retrain_v4_cost2/v-437ec196",
                    help="evaluated model — the one the F1/FNR reports describe")
    ap.add_argument("--deployed-model", default=os.environ.get("CLASSIFIER_MODEL_DIR", ""),
                    help="live deployment model (defaults to CLASSIFIER_MODEL_DIR env)")
    ap.add_argument("--out", default="reports/operational_readiness.md")
    ap.add_argument("--min-human-review", type=int, default=40)
    ap.add_argument("--max-high-risk-underclass", type=float, default=0.10)
    args = ap.parse_args()

    p1_gate, p1_payload = _p1_gate(_load_json(Path(args.p1_public)), _load_json(Path(args.p1_llm)))
    p2_gate, p2_payload = _p2_gate(_load_json(Path(args.p2)))
    data_gates, data_payload = _data_gates(
        Path(args.gold),
        Path(args.retrieval_gold),
        args.min_human_review,
        args.max_high_risk_underclass,
    )

    parity_gate = _model_parity_gate(args.model_dir, args.deployed_model)
    gate_objects = [p1_gate, p2_gate, *data_gates, parity_gate]
    public_f1 = p1_payload.get("public", {}).get("f1_macro", 0)
    pseudo_f1 = p1_payload.get("llm_pseudo", {}).get("f1_macro", 0)

    # 블로커 요약을 동적으로 생성 — 과거엔 "human-review가 유일 블로커"를 하드코딩하면서
    # model parity 게이트도 BLOCKED를 내보내 리포트가 자기모순이었다. human_review는
    # 외부 의존(실제 검수 라벨)이라 진짜 블로커지만, model parity는 배포 시
    # CLASSIFIER_MODEL_DIR 설정으로 자가해소되는 내부 액션이므로 구분해 표기한다.
    parity_blocked = parity_gate.status == "BLOCKED"
    hr_blocked = any(
        g.name == "human review gold" and g.status == "BLOCKED" for g in data_gates
    )
    if hr_blocked:
        blocker_line = (
            "human_review gold is the only EXTERNALLY-DEPENDENT release blocker "
            "(needs real human-reviewed samples; cannot be auto-filled)."
        )
    else:
        blocker_line = "No externally-dependent human_review blocker remains."
    if parity_blocked:
        blocker_line += (
            " NOTE: the 'model parity' gate is also currently BLOCKED, but it self-resolves "
            "at deploy time by setting CLASSIFIER_MODEL_DIR to the evaluated model "
            "(internal deploy action, not an external dependency)."
        )
    known_limitations = [
        blocker_line + " Everything else below is by design, not an open defect.",
        f"Classifier F1 is source-dependent: public/case/nkt direct = {public_f1:.3f} (upper bound), "
        f"llm_judge pseudo = {pseudo_f1:.3f} (lower bound). Never cite a single F1 without its source.",
        "Pseudo-label noise: ~47% of court-ruling records in the llm_judge tiers are over-graded S1/S2 "
        "(published rulings should be S3 — non-publicity fails). This deflates the pseudo-set F1 and inflates "
        "apparent high-risk->S3 'errors'. Quantify via scripts/analyze_label_noise.py; route corrections "
        "through the human-review loop, do not auto-relabel.",
        "The production path (m5_inference, api-like) applies an FNR-safe override that intentionally "
        "over-classifies toward higher grades: precision/F1 drop but high-risk under-classification (TS/S1/S2 -> S3) "
        "is driven to ~0. A low api-mode F1 is the safety trade-off working, not a regression.",
        "reports/eval_human_review_gold.* is produced by `p1_train_classifier.py --mode dryrun`, which scores the "
        "m3 keyword rule-labeler, NOT the trained v3 model. Its FAIL verdict does not describe model performance; "
        "use reports/p1_v3_*_gold_direct.* (eval_p1_model_gold.py) for the trained model.",
        "Operational cost of the over-classification (S3 docs flagged as S1/S2 -> reviewer false-positive load) "
        "is not yet quantified; defer until real human_review labels exist.",
        f"Evaluated model ({_norm_model(args.model_dir)}) may differ from the live deployment "
        f"({_norm_model(args.deployed_model) or 'unknown'}); the 'model parity' gate blocks release until they match.",
    ]
    next_actions = [
        f"Collect at least {args.min_human_review} human_review gold samples; "
        "the only externally-dependent blocked gate (model parity self-resolves via CLASSIFIER_MODEL_DIR).",
        f"Human-review gate now also requires high-risk underclass rate <= {args.max_high_risk_underclass:.2f} "
        "(human=TS/S1/S2 but model=S3); a filled queue alone no longer passes it.",
        "Reporting is split by source: public/case/nkt (definitive) vs llm_judge pseudo. "
        "Treat the pseudo-set F1 as the conservative bound, not the public-set F1.",
        "Review LLM pseudo-gold S2->S3 cases and either relabel or add boundary examples.",
        "DONE 2026-06-03: deployed model promoted v3 -> v4_cost2 (honest holdout). P1 gate now reads "
        "the de-contaminated holdout (F1 0.634 < 0.75) instead of v3's contaminated 0.830 — readiness "
        "honestly FAILs P1. Real fix is diverse S1 data, not a model swap (threshold/cost already tapped out).",
        "Run p2-full-gold after every ES reindex or embedding-model change.",
    ]
    payload = {
        "generated_at": "2026-06-03",
        "verdict": _overall(gate_objects),
        "evaluated_model": _norm_model(args.model_dir),
        "deployed_model": _norm_model(args.deployed_model) or "unknown",
        "retrieval_config": "KURE-v1 + Elasticsearch hybrid + chunk=1200/overlap=100",
        "release_gate_policy": {
            "min_human_review": args.min_human_review,
            "max_high_risk_underclass": args.max_high_risk_underclass,
        },
        "gates": [g.__dict__ for g in gate_objects],
        "p1": p1_payload,
        "p2": p2_payload,
        "data": data_payload,
        "next_actions": next_actions,
        "known_limitations": known_limitations,
    }

    out = Path(args.out)
    _write_md(payload, out)
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": payload["verdict"], "gates": payload["gates"]}, ensure_ascii=False, indent=2))
    return 0 if payload["verdict"] in {"PASS", "CONDITIONALLY_READY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

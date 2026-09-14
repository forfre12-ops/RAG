"""Offline paired S/V/M ablation. Diagnostic label agreement, never customer acceptance.

Production source, original datasets, model weights and operational settings are not edited.
Only a new report directory is written. Model logits are cached within each document so all
arms receive identical neural predictions; timings are NOT comparative serving latency.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import uuid

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))
sys.path.insert(0, str(POC / "scripts"))

from evaluation_inputs import normalized_hash, read_rows, sha256, text_of  # noqa: E402

GRADES = ("TS", "S1", "S2", "S3")
VARIANTS = ("current", "no_svm", "no_svm_no_agreement", "direct_model")
DEFAULT_SETS = {
    "hardened42": "datasets/gold_real/holdout_eval.hardened.jsonl",
    "holdout109": "datasets/gold_real/holdout_eval.jsonl",
    "golden100": "datasets/gold/golden100_labeled_v2.jsonl",
    "mundane150": "datasets/labeled_v8_mundane/mundane_holdout.jsonl",
    "proxy_development200": "datasets/proxy_eval/direct_authored_proxy_eval_split.v3/development_200.jsonl",
}
KNOWN_TRAIN_POOLS = (
    "datasets/labeled_p1_v5_clean/train.jsonl",
    "datasets/labeled_p1_v5_clean/val.jsonl",
    "datasets/labeled_p1_v5_clean/test.jsonl",
    "datasets/gold_real/train_subset.jsonl",
)


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_cases(path: Path, limit: int = 0) -> tuple[list[dict], dict]:
    from audit_eval_ground_truth import tier_of
    from measure_four_metrics import _label

    rows = read_rows(path)
    cases, ids = [], set()
    for index, row in enumerate(rows):
        body, truth = text_of(row), _label(row)
        if not body or truth not in GRADES:
            raise ValueError(f"Invalid text/label: {path.name}:{index + 1}")
        original_id = row.get("doc_id") or row.get("id")
        # File hash + physical JSON row number identifies records lacking an ID.
        doc_id = str(original_id or f"row-{index + 1}")
        if doc_id in ids:
            raise ValueError(f"Duplicate document ID: {path.name}:{index + 1}")
        ids.add(doc_id)
        cases.append({
            "doc_id": doc_id, "row_number": index + 1, "text": body, "truth": truth,
            "text_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "normalized_sha256": normalized_hash(body),
            "truth_tier": tier_of(row)[0], "label_source": tier_of(row)[1],
            "family_id": row.get("family_id") or row.get("document_family_id"),
            "origin": row.get("document_origin", "unknown"),
            "policy_version": row.get("policy_version"),
            "has_source_id": bool(original_id),
        })
    selected = cases[:limit] if limit else cases
    return selected, {
        "path": str(path), "sha256": sha256(path), "file_rows": len(cases),
        "measured_rows": len(selected), "grades": dict(Counter(c["truth"] for c in selected)),
        "truth_tiers": dict(Counter(c["truth_tier"] for c in selected)),
        "label_sources": dict(Counter(c["label_source"] or "missing" for c in selected)),
        "origins": dict(Counter(c["origin"] for c in selected)),
        "missing_policy_version": sum(not c["policy_version"] for c in selected),
        "missing_source_id": sum(not c["has_source_id"] for c in selected),
        "missing_family_id": sum(not c["family_id"] for c in selected),
        "within_set_duplicate_text_rows": len(selected) - len({c["normalized_sha256"] for c in selected}),
        "customer_acceptance_eligible": False,
    }


def metrics(rows: list[dict]) -> dict:
    from measure_four_metrics import compute

    result = compute(rows)
    per_grade = result["per_grade"]
    f1s = []
    for grade in GRADES:
        p, r = per_grade[grade]["precision"], per_grade[grade]["recall"]
        f1s.append(2 * p * r / (p + r) if p and r else 0.0)
    rank = {g: i for i, g in enumerate(GRADES)}
    eligible = [r for r in rows if r["status"] == "staging"]
    return {
        "n": len(rows), "agreement_hits": sum(r["truth"] == r["predicted"] for r in rows),
        "label_agreement": result["exact_grade_accuracy"]["rate"],
        "macro_f1_fixed_four_classes": sum(f1s) / 4,
        "per_grade": per_grade, "confusion_matrix": result["confusion_matrix"],
        "all_underclassified": sum(rank[r["predicted"]] > rank[r["truth"]] for r in rows),
        "all_overclassified": sum(rank[r["predicted"]] < rank[r["truth"]] for r in rows),
        "high_to_s3": sum(r["truth"] in ("TS", "S1") and r["predicted"] == "S3" for r in rows),
        "high_support": sum(r["truth"] in ("TS", "S1") for r in rows),
        "s2_to_s3": sum(r["truth"] == "S2" and r["predicted"] == "S3" for r in rows),
        "s2_support": sum(r["truth"] == "S2" for r in rows),
        "s1_s2_confusions": sum({r["truth"], r["predicted"]} == {"S1", "S2"} for r in rows),
        "review_n": sum(r["status"] == "needs_review" for r in rows),
        "not_routed_n": len(eligible),
        "not_routed_error_n": sum(r["truth"] != r["predicted"] for r in eligible),
        "not_routed_under_n": sum(rank[r["predicted"]] > rank[r["truth"]] for r in eligible),
        "not_routed_error_rate": (
            sum(r["truth"] != r["predicted"] for r in eligible) / len(eligible) if eligible else None
        ),
        "causal_review_reasons": dict(Counter(r.get("causal_review_reason") for r in rows
                                            if r["status"] == "needs_review")),
    }


def paired_delta(baseline: list[dict], candidate: list[dict]) -> dict:
    if len(baseline) != len(candidate):
        raise ValueError("Incomplete paired comparison")
    fixed = broken = changes = status_changes = 0
    for a, b in zip(baseline, candidate, strict=True):
        for key in ("doc_id", "truth", "text_sha256", "cleaned_sha256"):
            if a[key] != b[key]:
                raise ValueError(f"Paired input mismatch: {key}")
        fixed += a["predicted"] != a["truth"] and b["predicted"] == b["truth"]
        broken += a["predicted"] == a["truth"] and b["predicted"] != b["truth"]
        changes += a["predicted"] != b["predicted"]
        status_changes += a["status"] != b["status"]
    # Exact paired McNemar/binomial diagnostic; reference labels are not ground truth.
    from scipy.stats import binomtest
    discordant = fixed + broken
    return {"grade_changes": changes, "status_changes": status_changes,
            "fixed_relative_to_labels": fixed, "broken_relative_to_labels": broken,
            "agreement_delta_pp": 100 * (fixed - broken) / len(baseline),
            "paired_exact_p": float(binomtest(fixed, discordant, 0.5).pvalue) if discordant else 1.0,
            "p_value_note": "exploratory only; row independence unverified; no multiplicity correction; label agreement only"}


def prepare_settings(device: str) -> dict:
    # Set before importing config; never load actual DB or external inference services.
    os.environ.update(TESTING="1", DATABASE_URL="postgresql+psycopg://audit:audit@127.0.0.1:9/audit",
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                      DEPLOY_PROFILE="lite-noapi", OTEL_SDK_DISABLED="true",
                      CUBLAS_WORKSPACE_CONFIG=":4096:8")
    from koipa.config import Settings, _PROFILE_DEFAULTS, settings

    # Explicit field values override environment and .env. Keep the singleton identity.
    defaults = {k: f.get_default(call_default_factory=True) for k, f in Settings.model_fields.items()}
    clean = Settings(_env_file=None, **defaults)
    for key, value in clean:
        setattr(settings, key, value)
    profile = _PROFILE_DEFAULTS["onprem-local"]
    from measure_serving_records import PARITY_KEYS
    for key in PARITY_KEYS:
        setattr(settings, key, profile.get(key, getattr(clean, key)))
    settings.classifier_device = device
    settings.serving_model_refresh_seconds = 0
    settings.serving_prefer_active_model = False
    settings.factor_shadow_enabled = False
    settings.model_secondopinion_llm_enabled = False
    keys = [k for k in Settings.model_fields if k.startswith((
        "classifier_", "fnr_", "rule_", "review_confidence_", "source_prior_", "metadata_",
        "ts_tie_", "s2_", "factor_", "no_auto_confirm_", "model_secondopinion_", "severe_agg_",
    )) or k in ("max_seq_len", "chunk_overlap", "agreement_gate_enabled")]
    return {k: getattr(settings, k) for k in keys}


def run(args: argparse.Namespace) -> dict:
    out = Path(args.out).resolve()
    if out.exists():
        raise FileExistsError(f"Refusing to overwrite {out}")
    sets = {}
    for item in args.eval or []:
        name, sep, path = item.partition("=")
        if not sep or not name or name in sets:
            raise ValueError("--eval requires unique NAME=PATH")
        sets[name] = path
    sets = sets or DEFAULT_SETS
    model_dir = Path(args.model_dir).resolve()
    if not (model_dir / "model.safetensors").is_file():
        raise ValueError("Local safetensors model required; no fallback/download")
    loaded = {name: load_cases((POC / path).resolve(), args.limit) for name, path in sets.items()}
    settings_manifest = prepare_settings(args.device)
    from koipa.config import settings
    from koipa.modules.m3_labeling import rule_engine as rule_module
    from koipa.modules.m3_labeling.pipeline import LabelingPipeline, LabelingResult
    from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS
    from koipa.modules.m5_inference import pipeline as inference_module
    from koipa.schemas.classify import ClassifyRequest
    from koipa.services import classify_service as service_module
    from koipa.services.review_reasons import causal_review_reason
    import torch
    import transformers

    torch.manual_seed(20260914)
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    current = LabelingPipeline(rule_engine=rule_module.LabelRuleEngine(seeds=copy.deepcopy(KEYWORD_SEEDS)))
    no_svm = LabelingPipeline(rule_engine=rule_module.LabelRuleEngine(
        seeds=copy.deepcopy(KEYWORD_SEEDS), method="additive"))
    label_without_numeric_factors = no_svm.label

    def without_factors(text):
        lab = label_without_numeric_factors(text)
        lab.factors = None
        return lab

    no_svm.label = without_factors
    empty_labeler = SimpleNamespace(label=lambda text: LabelingResult(
        grade="S3", confidence=0, factors=None, evidence=[], rule_result=None))
    source_paths = [Path(__file__), POC / "scripts/evaluation_inputs.py",
                    POC / "scripts/measure_four_metrics.py", POC / "scripts/audit_eval_ground_truth.py"]
    source_paths += list((POC / "src/koipa/modules/m3_labeling").glob("*.py"))
    source_paths += list((POC / "src/koipa/modules/m5_inference").glob("*.py"))
    source_paths += list((POC / "src/koipa/modules/m2_preprocess").glob("*.py"))
    source_paths += [Path(service_module.__file__), POC / "src/koipa/config.py",
                     POC / "src/koipa/services/automation_assessment.py",
                     POC / "src/koipa/services/review_reasons.py",
                     POC / "src/koipa/schemas/classify.py", POC / "src/koipa/schemas/common.py",
                     POC / "src/koipa/golden_tiers.py"]
    model_paths = [p for p in model_dir.iterdir() if p.is_file()]
    train_paths = [POC / p for p in KNOWN_TRAIN_POOLS if (POC / p).is_file()]
    immutable_paths = {*source_paths, *model_paths, *train_paths,
                       *(Path(info["path"]) for _, info in loaded.values())}
    before = {str(p.resolve()): sha256(p) for p in sorted(immutable_paths)}
    train_hashes, train_families = set(), set()
    for path in train_paths:
        for row in read_rows(path):
            body = text_of(row)
            if body:
                train_hashes.add(normalized_hash(body))
            family = row.get("family_id") or row.get("document_family_id")
            if family:
                train_families.add(family)
    out.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": "svm-ablation-v1", "started_at": datetime.now(timezone.utc).isoformat(),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=POC, text=True).strip(),
        "model_dir": str(model_dir), "active_server_model_verified": False,
        "torch": torch.__version__, "transformers": transformers.__version__,
        "logit_cache_enabled": not args.no_logit_cache,
        "settings": settings_manifest, "input_and_code_sha256": before,
        "training_lineage_complete": False, "known_overlap_pool_paths": [str(p) for p in train_paths],
        "metadata_mode": "no caller metadata; stock body-derived effective metadata retained in A/B",
        "scope": "local service decision path with in-memory persistence stub; not HTTP/DB end-to-end",
        "variants": {
            "current": "stock inference + stock service gates; fixed code seeds and local checkpoint",
            "no_svm": "additive keyword grade, no numeric factors; keyword FNR and agreement gates retained",
            "no_svm_no_agreement": "no_svm plus agreement gate disabled; all other service gates retained",
            "direct_model": "same neural logits/temperature/aggregation/tau; inference postprocessing and agreement removed; service confidence review retained",
        },
        "limitations": [
            "Existing reference labels are not newly approved customer gold.",
            "No retraining; does not test a model trained with independently verified S/V/M inputs.",
            "Only known historical train/val/test pools checked; full lineage and semantic overlap unverified.",
            "No ACL evidence synthesized; missing evidence does not become proof of public status.",
            "Logits cached within each document: no comparative latency claim.",
            "staging means not routed to review in this harness, not persisted or human-confirmed.",
            "Outputs are exploratory diagnostics, not model promotion or deployment authorization.",
        ],
        "datasets": {name: info for name, (_, info) in loaded.items()},
    }
    write_json(out / "manifest.before.json", manifest)
    network_attempts = []

    def reject_network(*unused, **unused_kw):
        network_attempts.append("blocked")
        raise RuntimeError("Network disabled for offline ablation")

    counters = {v: {"formula_calls": 0, "forward_calls": 0, "cache_hits": 0} for v in VARIANTS}
    active = "current"
    original_formula = rule_module.grade_from_svm

    def count_formula(*items, **kwargs):
        counters[active]["formula_calls"] += 1
        if active != "current":
            raise RuntimeError("Numeric S/V/M unexpectedly used in ablated arm")
        return original_formula(*items, **kwargs)

    results, unique_texts = {}, set()
    start = time.monotonic()
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, "connect", reject_network))
        stack.enter_context(patch.object(socket, "create_connection", reject_network))
        stack.enter_context(patch.object(inference_module, "LabelingPipeline", lambda: current))
        stack.enter_context(patch.object(service_module, "_resolve_serving_model_dir", lambda: str(model_dir)))
        cls = service_module.ClassifyService
        stack.enter_context(patch.object(cls, "_maybe_refresh_model", lambda self: None))
        stack.enter_context(patch.object(cls, "_get_verified_label", lambda self, doc_id: None))
        stack.enter_context(patch.object(cls, "_ingestion_flagged_for_doc", lambda self, doc_id: False))
        stack.enter_context(patch.object(cls, "_try_persist", lambda self, *a, **kw:
                                         (uuid.uuid4(), [], kw["status"])))
        service = cls()
        pipe = service.inference
        if pipe._model is None:
            raise RuntimeError("Model was not loaded; rule fallback is forbidden")
        if args.device == "cuda" and pipe._device != "cuda":
            raise RuntimeError("Requested CUDA unavailable; refusing silent device change")
        manifest["actual_device"] = pipe._device
        manifest["effective_temperature"] = pipe._temperature
        manifest["id2label"] = {k: v.value for k, v in pipe._id2label.items()}
        manifest["effective_escalation_tau"] = pipe._escalation_tau
        original_run, original_forward = pipe.run, pipe._model.forward
        logit_cache = {}

        def cached_forward(*args_, **kwargs):
            if args_:
                raise ValueError("Unexpected positional neural model inputs")
            if args.no_logit_cache:
                counters[active]["forward_calls"] += 1
                return original_forward(**kwargs)
            key = tuple((k, tuple(v.shape), str(v.dtype), v.detach().cpu().numpy().tobytes())
                        for k, v in sorted(kwargs.items()))
            if key not in logit_cache:
                counters[active]["forward_calls"] += 1
                logit_cache[key] = original_forward(**kwargs).logits.detach().clone()
            else:
                counters[active]["cache_hits"] += 1
            return SimpleNamespace(logits=logit_cache[key])

        def direct_run(*, text, metadata=None, return_evidence=True):
            result = pipe._run_model(text, return_evidence)
            result.model_grade = result.label.value
            return result

        stack.enter_context(patch.object(pipe._model, "forward", cached_forward))
        stack.enter_context(patch.object(rule_module, "grade_from_svm", count_formula))
        for name, (cases, info) in loaded.items():
            print(f"[start] {name}: {len(cases)} records x {len(VARIANTS)} arms", flush=True)
            grouped = {v: [] for v in VARIANTS}
            info["known_pool_text_overlap_n"] = sum(c["normalized_sha256"] in train_hashes for c in cases)
            info["known_pool_family_overlap_n"] = sum(c["family_id"] in train_families
                                                       for c in cases if c["family_id"])
            info["cross_previous_set_duplicate_text_n"] = sum(c["normalized_sha256"] in unique_texts for c in cases)
            unique_texts.update(c["normalized_sha256"] for c in cases)
            rule_changed = 0
            rule_hits = Counter()
            for index, case in enumerate(cases):
                logit_cache.clear()
                cleaned = service.preprocess.run_text(case["text"])
                if not cleaned:
                    raise ValueError(f"Preprocessing produced empty text: {name}:{index}")
                active = "current"
                rule_a = current.engine.label(cleaned)
                rule_b = no_svm.engine.label(cleaned)
                rule_changed += rule_a.grade != rule_b.grade
                rule_hits["current"] += rule_a.grade == case["truth"]
                rule_hits["no_svm"] += rule_b.grade == case["truth"]
                request = ClassifyRequest(doc_id=f"svm-ablation-{name}-{index}", content=case["text"])
                for active in VARIANTS:
                    pipe.labeling = current if active == "current" else (
                        empty_labeler if active == "direct_model" else no_svm)
                    pipe.run = direct_run if active == "direct_model" else original_run
                    settings.agreement_gate_enabled = active in ("current", "no_svm")
                    response = service.classify(request)
                    if response.status not in ("staging", "needs_review") or response.model_grade not in GRADES:
                        raise ValueError("Invalid measured status/model grade")
                    if any("gate-fail-open" in w for w in response.warnings):
                        raise RuntimeError("A decision gate failed; cannot compare this measurement")
                    if network_attempts:
                        raise RuntimeError("Unexpected attempted external access; measurement invalid")
                    if active != "current" and response.evaluation_factors is not None:
                        raise RuntimeError("Numeric factors leaked into an ablated response")
                    record = {k: v for k, v in case.items() if k != "text"}
                    record.update(
                        variant=active, predicted=response.label.value, model_grade=response.model_grade,
                        status=response.status, confidence=response.confidence, scores=response.scores,
                        cleaned_sha256=hashlib.sha256(cleaned.encode()).hexdigest(),
                        rule_grade=response.rule_grade, warnings=response.warnings,
                        causal_review_reason=causal_review_reason(response.warnings, response.status),
                        numeric_factors_present=response.evaluation_factors is not None,
                    )
                    grouped[active].append(record)
                if len({grouped[v][-1]["model_grade"] for v in VARIANTS}) != 1:
                    raise RuntimeError("Neural model prediction changed across paired arms")
                if (index + 1) % 25 == 0:
                    print(f"[progress] {name} {index + 1}/{len(cases)} elapsed={time.monotonic()-start:.1f}s", flush=True)
            for variant, records in grouped.items():
                with (out / f"{name}.{variant}.records.jsonl").open("x", encoding="utf-8") as handle:
                    for record in records:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            results[name] = {
                "inputs": info, "arms": {v: metrics(rs) for v, rs in grouped.items()},
                "paired_vs_current": {v: paired_delta(grouped["current"], grouped[v]) for v in VARIANTS[1:]},
                "rule_only": {"grade_changes": rule_changed, "agreement_hits": dict(rule_hits)},
                "known_pool_nonoverlap_arms": {
                    v: metrics([r for r in rs if r["normalized_sha256"] not in train_hashes])
                    for v, rs in grouped.items()
                    if any(r["normalized_sha256"] not in train_hashes for r in rs)
                },
            }
            write_json(out / f"{name}.summary.json", results[name])
            print(f"[done] {name}: " + json.dumps(results[name]["paired_vs_current"]), flush=True)
    for path, digest in before.items():
        if sha256(Path(path)) != digest:
            raise RuntimeError(f"Input or code changed during measurement: {path}")
    for variant in VARIANTS[1:]:
        if counters[variant]["formula_calls"] or (
            not args.no_logit_cache and counters[variant]["forward_calls"]
        ):
            raise RuntimeError("Ablation isolation failed")
    report = {"manifest": manifest, "results": results, "counters": counters,
              "unique_normalized_texts": len(unique_texts), "network_attempts": len(network_attempts),
              "unchanged_input_and_code_files": len(before), "elapsed_seconds": time.monotonic() - start,
              "customer_acceptance": "NOT_ASSESSED", "completed_at": datetime.now(timezone.utc).isoformat()}
    write_json(out / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--eval", action="append")
    parser.add_argument("--no-logit-cache", action="store_true",
                        help="Re-run neural inference independently in every arm to validate cached measurements")
    parser.add_argument("--limit", type=int, default=0, help="First N per set, smoke testing only")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    logging.basicConfig(level=logging.ERROR)
    report = run(args)
    print(json.dumps({"output": args.out, "n": sum(r["arms"]["current"]["n"]
                     for r in report["results"].values()), "counters": report["counters"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Original260 diagnostic: existing checkpoint through the local serving endpoint.

No training, source-pack permission changes, deployment, or gold promotion.
Reuses measure_serving_records profile configuration and customer_benchmark text.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import socket
import statistics
import sys
import time

from measure_serving_records import PARITY_KEYS, _env_value, _git, _profile_expected

POC = Path(__file__).resolve().parents[1]
SOURCE = POC / "reports/CUSTOMER_GUIDE_PARALLEL02_20260915/reference_v0_6"
MODEL = POC / "artifacts/classifier_p1_v5_clean/v-fe4b386b"
GRADES = ("TS", "S1", "S2", "S3")
CONDITIONS = ("body_only", "context_only", "body_context", "body_icd")


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")


def icd_from_premises(p):
    # Never inspect reference_grade, decision.factors, or generate a security marking.
    md = {}
    if p.get("public_exact_body") is True:
        md["source_type"] = "public"
    if p.get("access_enforced") is True:
        if p.get("individual_approval") is True:
            md["access_scope"] = "approved_only"
        elif p.get("business_need_only") is True:
            md["access_scope"] = "designated"
    # all_staff_knows means knowledge/need, not an explicit all-employees ACL.
    # Do not invent all_employees, internal source, or security_marking=none.
    return md


def metrics(rows, field):
    n = len(rows)
    truths = [r["truth"] for r in rows]
    predictions = [r.get(field) for r in rows]
    correct = sum(t == p for t, p in zip(truths, predictions))
    perms = []
    for seed in range(1000):
        shuffled = truths.copy()
        random.Random(seed).shuffle(shuffled)
        perms.append(sum(t == p for t, p in zip(shuffled, predictions)) / n)
    confusion = {g: dict(Counter(r.get(field) or "ERROR" for r in rows if r["truth"] == g)) for g in GRADES}
    severe = [r for r in rows if r["truth"] in {"TS", "S1"} and r.get(field) == "S3"]
    s3 = [r for r in rows if r["truth"] == "S3"]
    return {
        "correct": correct, "n": n, "accuracy": correct / n,
        "recall": {g: confusion[g].get(g, 0) / truths.count(g) if g in truths else None for g in GRADES},
        "confusion": confusion,
        "s1_s2_confusion": sum({r["truth"], r.get(field)} == {"S1", "S2"} for r in rows),
        "s2_to_s3": sum(r["truth"] == "S2" and r.get(field) == "S3" for r in rows),
        "ts_s1_to_s3": len(severe),
        "ts_s1_to_s3_without_review": sum(r.get("status") != "needs_review" for r in severe),
        "s3_overclassified": sum(r.get(field) in {"TS", "S1", "S2"} for r in s3),
        "prediction_counts": dict(Counter(predictions)),
        "shuffled_label_baseline": {"runs": 1000, "seed_range": [0, 999], "mean": statistics.mean(perms),
                                    "min": min(perms), "max": max(perms),
                                    "p_ge_observed_plus_one": (1 + sum(x >= correct / n for x in perms)) / 1001},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0, help="Smoke only; full diagnostic uses all 260")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(filename=out / "runtime.log", level=logging.WARNING, force=True)
    os.environ.update(TESTING="1", RATE_LIMIT_DISABLED="1", API_KEY="original260-local-diagnostic",
                      CLASSIFIER_MODEL_DIR=str(MODEL), CLASSIFIER_DEVICE="cpu",
                      DATABASE_URL="postgresql+psycopg://measurement:measurement@127.0.0.1:1/measurement",
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      TOKENIZERS_PARALLELISM="false")
    expected = _profile_expected("onprem-local", PARITY_KEYS)
    for key, value in expected.items():
        encoded = _env_value(value)
        if encoded is None:
            os.environ.pop(key.upper(), None)
        else:
            os.environ[key.upper()] = encoded

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def loopback_only(original):
        def connect(sock, address):
            # Windows asyncio uses a loopback socket pair for its event loop.
            if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
                raise OSError("External network disabled for original260 local diagnostic")
            return original(sock, address)
        return connect
    socket.socket.connect = loopback_only(original_connect)
    socket.socket.connect_ex = loopback_only(original_connect_ex)

    import torch
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    from fastapi.testclient import TestClient
    from koipa.api.app import app
    from koipa.config import settings
    from koipa.customer_benchmark import presented_text, validate_documents
    from koipa.services.classify_service import ClassifyService
    from koipa.services.review_reasons import causal_review_reason, gate_hits

    effective = {k: getattr(settings, k) for k in PARITY_KEYS}
    if effective != expected:
        raise RuntimeError(f"Profile drift: {effective} != {expected}")
    source_files = [SOURCE / x for x in ("manifest.json", "policy.json", "authoring/documents.jsonl",
                                        "answers/answers.candidate.jsonl", "answers/evidence.jsonl")]
    tracked = source_files + sorted(p for p in MODEL.iterdir() if p.is_file())
    tracked += [Path(__file__).resolve(), POC / "scripts/measure_serving_records.py"]
    tracked += sorted((POC / "src/koipa").rglob("*.py"))
    hashes_before = {str(p): sha(p) for p in tracked}
    if hashes_before[str(SOURCE / "manifest.json")] != "7e8ea7552e272f5391f1d95113176f955f8828352d38f45cfc24b979eb3c8f95":
        raise RuntimeError("Original260 source manifest changed")
    docs = validate_documents(read_rows(SOURCE / "authoring/documents.jsonl"))
    answers = {r["doc_id"]: r for r in read_rows(SOURCE / "answers/answers.candidate.jsonl")}
    evidence = {r["doc_id"]: r for r in read_rows(SOURCE / "answers/evidence.jsonl")}
    if len(docs) != 260 or {d.input.doc_id for d in docs} != set(answers) or set(answers) != set(evidence):
        raise RuntimeError("Original260 IDs/count mismatch")
    docs = docs[:args.limit] if args.limit else docs
    provenance = {"scope": "synthetic_candidate_diagnostic_not_customer_accuracy", "training_executed": False,
                  "source_permissions_modified": False, "gold_promoted": False,
                  "authorization": "2026-09-15 user 진행 following actual-model diagnostic proposal",
                  "source": str(SOURCE), "model_dir": str(MODEL), "git_head": _git("rev-parse", "HEAD"),
                  "git_dirty": bool(_git("status", "--porcelain")), "hashes_before": hashes_before,
                  "profile": "onprem-local", "effective_settings": effective,
                  "runtime_scope": "current checkout in-process API; no DB rules, external services or lifespan startup",
                  "icd_mapping": "public_exact_body:true -> public; enforced individual approval -> approved_only; enforced need-only -> designated; other fields omitted",
                  "device": "cpu", "torch_threads": 4, "n_per_condition": len(docs)}
    dump(out / "provenance.json", provenance)
    print(f"Loading actual checkpoint on CPU; {len(docs)} x 4 requests", flush=True)
    started = time.monotonic()
    svc = ClassifyService.get_instance()
    pipeline = svc.inference
    if pipeline._model is None:
        raise RuntimeError("Actual checkpoint unavailable; rule fallback is not a model measurement")
    provenance["temperature"] = pipeline._temperature
    provenance["escalation_tau"] = pipeline._escalation_tau
    provenance["grade_formula_mode"] = settings.grade_formula_mode
    provenance["max_seq_len"] = settings.max_seq_len
    provenance["chunk_overlap"] = settings.chunk_overlap
    label_order = {k: getattr(v, "value", str(v)) for k, v in pipeline._id2label.items()}
    provenance["id2label"] = label_order
    dump(out / "provenance.json", provenance)
    observed = []
    aggregate = []
    original_aggregate = pipeline._aggregate_chunk_probs

    def capture_aggregate(*args, **kwargs):
        result = original_aggregate(*args, **kwargs)
        aggregate.append(result.detach().cpu().tolist())
        return result
    pipeline._aggregate_chunk_probs = capture_aggregate

    def capture_forward(_module, _inputs, output):
        observed.extend(output.logits.detach().cpu().tolist())
    hook = pipeline._model.register_forward_hook(capture_forward)
    client = TestClient(app)
    records = []
    with (out / "records.jsonl").open("x", encoding="utf-8") as stream:
        for condition in CONDITIONS:
            for i, doc in enumerate(docs):
                doc_id = doc.input.doc_id
                full = presented_text(doc, "body_context")
                content = full if condition == "body_context" else full[len(doc.input.text):] if condition == "context_only" else doc.input.text
                metadata = icd_from_premises(evidence[doc_id]["premises"]) if condition == "body_icd" else {}
                payload = {"doc_id": f"diagnostic260-{condition}-{i:04d}", "content": content, "metadata": metadata}
                observed.clear()
                aggregate.clear()
                record = {"doc_id": doc_id, "condition": condition, "truth": answers[doc_id]["reference_grade"],
                          "family_id": doc.family_id, "request": payload,
                          "text_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                          "text_chars": len(content), "input_sha256": doc.input_sha256,
                          "policy_sha256": answers[doc_id]["policy_sha256"]}
                t0 = time.monotonic()
                try:
                    response = client.post("/api/v1/classify", headers={"X-API-Key": settings.api_key}, json=payload)
                    response.raise_for_status()
                    result = response.json()
                    record.update(response=result, model_grade=result.get("model_grade"), predicted=result["label"],
                                  status=result["status"], confidence=result["confidence"],
                                  causal_review_reason=causal_review_reason(result.get("warnings", []), result["status"]),
                                  review_gate_hits=gate_hits(result.get("warnings", [])))
                    if not observed or result.get("model_grade") not in GRADES:
                        raise RuntimeError("No actual model forward / model grade for this request")
                    if len(aggregate) != 1:
                        raise RuntimeError(f"Expected one document probability aggregation, got {len(aggregate)}")
                    record["raw_argmax"] = label_order[max(range(len(aggregate[0])), key=lambda k: aggregate[0][k])]
                    record["aggregated_probabilities"] = aggregate[0]
                except Exception as exc:
                    record.update(error=f"{type(exc).__name__}: {exc}", status="ERROR")
                record.update(forward_windows=len(observed), window_logits=list(observed), elapsed_seconds=time.monotonic() - t0)
                records.append(record)
                stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                if (i + 1) % 20 == 0 or i + 1 == len(docs):
                    print(f"{condition}: {i+1}/{len(docs)}; total elapsed {time.monotonic()-started:.1f}s; errors={sum('error' in r for r in records)}", flush=True)
                if "error" in record and not observed:
                    raise RuntimeError(f"Measurement stopped; partial record retained: {record['error']}")
    hook.remove()
    pipeline._aggregate_chunk_probs = original_aggregate
    client.close()
    changed = [str(p) for p in tracked if sha(p) != hashes_before[str(p)]]
    summary = {"complete": len(records) == len(docs) * 4, "n_records": len(records),
               "errors": sum("error" in r for r in records), "changed_source_files": changed,
               "elapsed_seconds": time.monotonic() - started, "records_sha256": sha(out / "records.jsonl"), "conditions": {}}
    for condition in CONDITIONS:
        rows = [r for r in records if r["condition"] == condition]
        summary["conditions"][condition] = {
            "raw_argmax": metrics(rows, "raw_argmax"),
            "model": metrics(rows, "model_grade"), "serving": metrics(rows, "predicted"),
            "review_count": sum(r["status"] == "needs_review" for r in rows),
            "statuses": dict(Counter(r["status"] for r in rows)),
            "review_reasons": dict(Counter(r.get("causal_review_reason") for r in rows)),
            "forward_windows": dict(Counter(r["forward_windows"] for r in rows)),
            "model_versions": sorted({r.get("response", {}).get("model_version", "ERROR") for r in rows})}
    dump(out / "summary.json", summary)
    print(json.dumps({k: {"model": v["model"]["accuracy"], "serving": v["serving"]["accuracy"], "review": v["review_count"]} for k, v in summary["conditions"].items()}, ensure_ascii=False), flush=True)
    return 0 if not changed and not summary["errors"] else 2


if __name__ == "__main__":
    sys.exit(main())

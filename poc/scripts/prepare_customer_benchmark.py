"""Prepare a new synthetic customer-trial workspace; never train or bless gold.

prepare produces the first authored manuscripts, not 1,000 completed documents.
split/score accept separately supplied draft answers and remain diagnostic only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from customer_benchmark_drafts import build_drafts
from koipa.customer_benchmark import (FLAGS, TARGET, audit_external_pool, duplicate_audit, presented_text,
    propose_split, score_predictions, strict_loads, validate_documents)
from koipa.policy_facts import require, text_digest, value_digest

SOURCES = ("src/koipa/customer_benchmark.py", "src/koipa/policy_facts.py", "src/koipa/dataset_usage.py",
           "src/koipa/policy_fixture_registry.json", "scripts/customer_benchmark_drafts.py",
           "scripts/prepare_customer_benchmark.py", "docs/CUSTOMER_BENCHMARK_800_200_V1.md")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _jsonl(records):
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for r in records)


def _rows(data):
    require(bool(data.strip()), "benchmark_jsonl_empty")
    return [strict_loads(line) for line in data.decode("utf-8").splitlines()]


def _new_file(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def token_audit(docs, path):
    if path is None:
        return {"status": "not_run", "model_inference_performed": False}
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    values = []
    for d in docs:
        for profile in ("body_only", "body_context"):
            text = presented_text(d, profile)
            encoded = tokenizer.encode(text)
            values.append({"doc_id": d.input.doc_id, "profile": profile, "tokens_with_special": len(encoded.ids),
                           "fits_512_tokens": len(encoded.ids) <= 512, "presented_sha256": text_digest(text)})
    return {"status": "measured", "tokenizer_path": str(path.resolve()),
            "tokenizer_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "views": values,
            "semantic_evidence_coverage_verified": False, "model_inference_performed": False,
            "active_serving_model_verified": False}


def summarize(docs, audit, external, tokens):
    return {**FLAGS, "schema_version": "customer-benchmark-preparation-summary-v1", "status": "authoring_started_not_released",
            "target": TARGET, "target_documents": 1000, "authored_documents": len(docs),
            "claims_with_valid_text_bindings": sum(len(d.claims) for d in docs),
            "semantically_certified_claims": 0, "fixed_grade_answers": 0,
            "accepted_training_documents": 0, "accepted_evaluation_documents": 0,
            "policy_selection": None, "customer_protocol_accepted": False,
            "domains": dict(Counter(d.domain for d in docs)), "connected_lineage_groups": len(audit["groups"]),
            "exact_duplicate_pairs": len(audit["exact_pairs"]), "number_only_pairs": len(audit["number_only_pairs"]),
            "near_duplicate_pairs": len(audit["near_pairs"]), "known_fixture_matches": len(audit["known_fixture_body_matches"]),
            "external_pool_scan_status": "measured" if external else "not_run",
            "external_pool_files": len(external["files"]) if external else 0,
            "external_pool_matches": len(external["matches"]) if external else None,
            "external_pool_coverage_complete": external["coverage_complete"] if external else False,
            "tokenizer_scan_status": tokens["status"],
            "remaining": ["Select and version the evaluation policy; customer adoption not implied by authoring request.",
                "Complete diverse manuscripts and explicit fictional context; current unknowns are not false.",
                "Verify semantic facts and independently calculate one reference grade with exclusion reasons.",
                "Review full-corpus/template shortcuts, source-pool coverage and meaningful near duplicates.",
                "Create an exact connected-group-disjoint 800/200 split and freeze input, gold and model contracts.",
                "Authorize only a new release; existing fixtures, production models and golden tiers stay unchanged."],
            "manufactured_human_signatures": 0, "model_trained": False, "classification_accuracy_measured": False}


def prepare(out, *, corpus_root=None, tokenizer_path=None):
    require(not out.exists(), "benchmark_output_exists")
    records = build_drafts()
    docs = validate_documents(records)
    audit = duplicate_audit(docs)
    external = audit_external_pool(docs, corpus_root.rglob("*.jsonl")) if corpus_root else None
    tokens = token_audit(docs, tokenizer_path)
    summary = summarize(docs, audit, external, tokens)
    input_rows = [{**FLAGS, "doc_id": d.input.doc_id, "text": d.input.text,
                   "context": [c.model_dump() for c in d.input.context], "input_sha256": d.input_sha256} for d in docs]
    payload = {"authoring/drafts.jsonl": _jsonl(records), "inputs/draft_inputs.jsonl": _jsonl(input_rows),
        "audit/duplicates.json": _json(audit), "audit/external_pool.json": _json(external), "audit/tokenizer.json": _json(tokens),
        "summary.json": _json(summary),
        "answers/README.md": "# 정답지 상태\n\n현재 고정 등급 정답은 0건입니다. 문서 초안과 본문 근거만 있으며, "
            "채점 정책을 선택하지 않았습니다. 여기에 정답 없는 빈 JSONL을 만들어 골든셋으로 세지 않습니다.\n",
        "README.md": "# 고객사 합성 문서 시험 준비\n\n목표는 학습 800건 + 평가 200건입니다. "
            "현재는 별도로 집필한 초안 12건이며, 학습/평가 채택 0건입니다.\n\n"
            "문서와 가상 맥락은 inputs, 근거 후보는 authoring, 검사 결과는 audit에 분리했습니다. "
            "기존 제한 자료를 재분할하거나 사용 금지를 해제하지 않았습니다.\n\n"
            "실제 고객 정보가 없는 합성 원고입니다. 본문 근거 위치 검사는 의미·등급 검증이 아닙니다. "
            "고객사 최종 승인, 외부 독립 골든셋, 실제 분류 성능을 주장하지 않습니다.\n"}
    for d in docs:
        payload[f"documents/{d.input.doc_id}.txt"] = d.input.text
    source_hashes = {p: hashlib.sha256((POC / p).read_bytes()).hexdigest() for p in SOURCES}
    out.mkdir(parents=True, exist_ok=False)
    for name, text in payload.items():
        _new_file(out / name, text)
    manifest = {**FLAGS, "schema_version": "customer-benchmark-preparation-pack-v1",
                "policy_selection": None, "dataset_role": "synthetic_customer_benchmark_draft",
                "source_files_sha256": source_hashes, "files": {n: text_digest(t) for n, t in payload.items()}}
    _new_file(out / "manifest.json", _json(manifest))
    return verify(out)


def verify(root):
    root = root.resolve()
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = strict_loads(manifest_bytes.decode("utf-8"))
    require(manifest["schema_version"] == "customer-benchmark-preparation-pack-v1" and manifest["policy_selection"] is None,
            "benchmark_manifest_invalid")
    require(value_digest({k: manifest[k] for k in FLAGS}) == value_digest(FLAGS), "benchmark_manifest_flags_invalid")
    require(set(manifest["source_files_sha256"]) == set(SOURCES), "benchmark_source_list_invalid")
    for name, sha in manifest["source_files_sha256"].items():
        require(hashlib.sha256((POC / name).read_bytes()).hexdigest() == sha, "benchmark_source_drift")
    files = manifest["files"]
    require(isinstance(files, dict) and bool(files), "benchmark_manifest_empty")
    data = {}
    for name, sha in files.items():
        path = (root / name).resolve()
        require(not Path(name).is_absolute() and "\\" not in name and path.is_relative_to(root) and path != root, "benchmark_path_escape")
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == sha, "benchmark_file_hash_mismatch")
        data[name] = raw
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} == set(files) | {"manifest.json"}, "benchmark_unlisted_file")
    records = _rows(data["authoring/drafts.jsonl"])
    require(value_digest(records) == value_digest(build_drafts()), "benchmark_drafts_replay_mismatch")
    docs = validate_documents(records)
    expected_files = {"authoring/drafts.jsonl", "inputs/draft_inputs.jsonl", "audit/duplicates.json", "audit/external_pool.json",
                      "audit/tokenizer.json", "summary.json", "README.md", "answers/README.md"} | {f"documents/{d.input.doc_id}.txt" for d in docs}
    require(set(files) == expected_files, "benchmark_payload_list_invalid")
    expected_inputs = [{**FLAGS, "doc_id": d.input.doc_id, "text": d.input.text,
                        "context": [c.model_dump() for c in d.input.context], "input_sha256": d.input_sha256} for d in docs]
    require(value_digest(_rows(data["inputs/draft_inputs.jsonl"])) == value_digest(expected_inputs), "benchmark_input_projection_invalid")
    audit = duplicate_audit(docs)
    require(value_digest(strict_loads(data["audit/duplicates.json"].decode("utf-8"))) == value_digest(audit), "benchmark_duplicate_audit_mismatch")
    external = strict_loads(data["audit/external_pool.json"].decode("utf-8"))
    # Never read paths declared by an untrusted report during verification.
    # External scan is a hashed historical observation; --prepare reruns it live.
    tokens = strict_loads(data["audit/tokenizer.json"].decode("utf-8"))
    if tokens["status"] == "measured":
        require(len(tokens["views"]) == 2 * len(docs), "benchmark_token_view_count_invalid")
        for d in docs:
            for profile in ("body_only", "body_context"):
                matched = [v for v in tokens["views"] if v["doc_id"] == d.input.doc_id and v["profile"] == profile]
                require(len(matched) == 1 and matched[0]["presented_sha256"] == text_digest(presented_text(d, profile)), "benchmark_token_input_mismatch")
    summary = summarize(docs, audit, external, tokens)
    require(value_digest(strict_loads(data["summary.json"].decode("utf-8"))) == value_digest(summary), "benchmark_summary_mismatch")
    for d in docs:
        require(data[f"documents/{d.input.doc_id}.txt"].decode("utf-8") == d.input.text, "benchmark_export_text_mismatch")
    require((root / "manifest.json").read_bytes() == manifest_bytes, "benchmark_pack_changed_during_read")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--corpus-root", type=Path)
    build.add_argument("--tokenizer", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("--pack", type=Path, required=True)
    for command in ("split", "score"):
        run = commands.add_parser(command)
        run.add_argument("--documents", type=Path, required=True)
        run.add_argument("--answers", type=Path, required=True)
        run.add_argument("--out", type=Path, required=True)
        if command == "split":
            run.add_argument("--seed", type=int, default=20260915)
        else:
            run.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.out, corpus_root=args.corpus_root, tokenizer_path=args.tokenizer)
        elif args.command == "verify":
            result = verify(args.pack)
        else:
            require(not args.out.exists(), "benchmark_output_exists")
            docs, answers = _rows(args.documents.read_bytes()), _rows(args.answers.read_bytes())
            result = (propose_split(docs, answers, seed=args.seed) if args.command == "split" else
                      score_predictions(docs, answers, _rows(args.predictions.read_bytes())))
            _new_file(args.out, _json(result))
        print(json.dumps({k: result[k] for k in ("status", "authored_documents", "fixed_grade_answers", "denominator",
                            "grade_agreement_all_gold", "run_complete", "customer_size_contract_met") if k in result}, ensure_ascii=False))
        return 2 if args.command == "score" and (not result["run_complete"] or not result["customer_size_contract_met"]) else 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        print(json.dumps({"status": "failed", "code": "customer_benchmark_command_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

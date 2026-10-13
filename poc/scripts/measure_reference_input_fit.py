"""Token/requirement visibility audit. No model weights, logits or grade oracle.

Reuses the local normalizer and chunker by file, avoiding package side effects.
Backend tokenization is a diagnostic profile, not an execution of the service.
All answer/evidence fields stay outside serialized model inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from build_internal_reference import _json, _jsonl, _loads, verify_pack
from koipa.policy_facts import require, text_digest, value_digest
from short_reference_probes import FLAGS, VERSION, build_probes


def load_local_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, POC / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def proposed_view(raw):
    """Explicit experimental serialization. NOT the current classifier input."""
    return raw["text"] + "\n[제공된 가상 맥락]\n" + json.dumps(raw["context"], ensure_ascii=False, sort_keys=True)


def validate_pair(raw, annotation):
    require(set(raw) == {"doc_id", "text", "document_sha256", "context"}, "fit_input_schema_invalid")
    require(raw["doc_id"] == annotation["doc_id"] and value_digest(raw) == annotation["input_sha256"], "fit_input_binding_invalid")
    require(text_digest(raw["text"]) == raw["document_sha256"] and bool(raw["text"].strip()), "fit_body_invalid")
    require(isinstance(raw["context"], dict), "fit_context_invalid")
    require(type(annotation["requires_full_body"]) is bool, "fit_full_body_flag_invalid")
    require(annotation["requirements"], "fit_requirements_empty")
    require(annotation["proposed_grade"] in {"TS", "S1", "S2", "S3", None}, "fit_grade_invalid")
    seen = set()
    for req in annotation["requirements"]:
        require(req["id"] not in seen, "fit_duplicate_requirement")
        seen.add(req["id"])
        if req["kind"] == "body":
            start, end = req["start"], req["end"]
            require(type(start) is type(end) is int and 0 <= start < end <= len(raw["text"]), "fit_span_invalid")
            require(text_digest(raw["text"][start:end]) == req["sha256"], "fit_span_hash_invalid")
        else:
            require(req["kind"] == "context" and req["pointer"].startswith("/") and req["pointer"].count("/") == 1, "fit_pointer_invalid")
            key = req["pointer"][1:]
            require(key in raw["context"] and value_digest(raw["context"][key]) == req["value_sha256"], "fit_context_binding_invalid")


def chunk_positions(text, chunks):
    """Map unchanged parts/tails; fail rather than invent offsets after rewrites."""
    cursor, previous = 0, []
    result = []
    for chunk in chunks:
        n = chunk.overlap_prev
        part = chunk.text[n:]
        start = text.find(part, cursor)
        require(start >= 0 and (not n or n <= len(previous)), "fit_chunk_not_exactly_mappable")
        positions = (previous[-n:] if n else []) + list(range(start, start + len(part)))
        require("".join(text[i] for i in positions) == chunk.text, "fit_chunk_mapping_invalid")
        result.append(positions)
        previous = list(range(start, start + len(part)))
        cursor = start + len(part)
    return result


def collision_bound(rows):
    """Empirical ceiling for a deterministic classifier of EXACTLY this input view."""
    groups = defaultdict(list)
    for row in rows:
        if row["grade"] is not None:
            groups[row["feature_sha256"]].append((row["doc_id"], row["grade"]))
    n = sum(map(len, groups.values()))
    correct = sum(max(Counter(g for _, g in members).values()) for members in groups.values())
    conflicts = [members for members in groups.values() if len({g for _, g in members}) > 1]
    return {"grade_rows": n, "distinct_inputs": len(groups), "conflicting_input_groups": len(conflicts),
            "rows_in_conflict": sum(map(len, conflicts)), "max_correct_on_this_fixed_table": correct,
            "deterministic_accuracy_ceiling": correct / n if n else None,
            "conflicts": conflicts, "measured_model_accuracy": False}


def inspect_view(raw, annotation, tokenizer, normalize, splitter, *, max_length=512, overlap=64, profile):
    validate_pair(raw, annotation)
    require(type(max_length) is type(overlap) is int and max_length >= 16 and 0 <= overlap < max_length, "fit_limits_invalid")
    include_context = profile == "proposed_body_context_first"
    require(profile in {"train_body_first", "serving_body_truncated", "serving_body_overflow", "proposed_body_context_first"}, "fit_profile_invalid")
    source = proposed_view(raw) if include_context else raw["text"]
    text = normalize(source)
    normalization_retained_content = (re.sub(r"\s+", "", unicodedata.normalize("NFKC", source)) ==
                                      re.sub(r"\s+", "", text))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    full = tokenizer.encode(text)
    full_offsets = {(a, b) for a, b in full.offsets if b > a}
    content_positions = {p for a, b in full_offsets for p in range(a, b) if not text[p].isspace()}
    unknowns = sum(token == "[UNK]" for token in full.tokens)
    if profile in {"train_body_first", "proposed_body_context_first"}:
        parts, maps = [text], [list(range(len(text)))]
    else:
        chunks = splitter(text, size=max_length * 3, overlap=overlap)
        parts, maps = [c.text for c in chunks], chunk_positions(text, chunks)
    overflow = profile == "serving_body_overflow"
    tokenizer.enable_truncation(max_length=max_length, stride=min(overlap, max_length // 4) if overflow else 0)
    window_positions, window_ids = [], []
    for part, positions in zip(parts, maps, strict=True):
        first = tokenizer.encode(part)
        for enc in [first, *first.overflowing] if overflow else [first]:
            visible = {positions[p] for a, b in enc.offsets if b > a for p in range(a, b) if not part[p].isspace()}
            window_positions.append(visible)
            window_ids.append(enc.ids)
    tokenizer.no_truncation()
    covered = set().union(*window_positions)
    details = []
    for req in annotation["requirements"]:
        if req["kind"] == "body":
            snippet = normalize(raw["text"][req["start"]:req["end"]])
        else:
            key = req["pointer"][1:]
            snippet = json.dumps(key, ensure_ascii=False) + ": " + json.dumps(raw["context"][key], ensure_ascii=False)
        present = include_context or req["kind"] == "body"
        hits = text.count(snippet) if present and snippet else 0
        if hits != 1:
            details.append({"id": req["id"], "visible": False, "single_window": False,
                            "reason": "absent_or_ambiguous_after_normalization"})
            continue
        start = text.index(snippet)
        wanted = {p for p in range(start, start + len(snippet)) if not text[p].isspace()}
        visible = wanted <= covered
        details.append({"id": req["id"], "visible": visible,
                        "single_window": any(wanted <= win for win in window_positions),
                        "reason": None if visible else "required_characters_not_exposed"})
    # Coverage counts observed offsets, not semantic comprehension. UNK is separate.
    full_visible = content_positions <= covered
    satisfied = (all(r["visible"] for r in details) and unknowns == 0 and
                 (not annotation["requires_full_body"] or (full_visible and normalization_retained_content)))
    return {"doc_id": raw["doc_id"], "profile": profile, "input_text_sha256": text_digest(text),
            "feature_sha256": value_digest(window_ids), "characters": len(text), "full_token_count": len(full.ids),
            "unk_tokens": unknowns, "chunks": len(parts), "windows": len(window_ids),
            "normalization_retained_non_whitespace_content": normalization_retained_content,
            "full_token_offset_coverage": full_visible, "offset_character_count": len(content_positions),
            "covered_offset_characters": len(content_positions & covered),
            "requirements_visible": sum(r["visible"] for r in details), "requirements_total": len(details),
            "all_required_input_observed": satisfied, "context_serialized": include_context, "requirements": details,
            "single_window_contains_entire_view": any(content_positions <= win for win in window_positions),
            "semantic_grade_verified": False, "grade_metric_eligible": False}


def load_ledger(pack):
    verify_pack(pack)
    inputs = [_loads(line)["input"] for line in (pack / "inputs.jsonl").read_text(encoding="utf-8").splitlines()]
    answers = {_loads(line)["certificate"]["doc_id"]: _loads(line)["certificate"]
               for line in (pack / "answers.jsonl").read_text(encoding="utf-8").splitlines()}
    output = []
    for record in inputs:
        raw = {k: record[k] for k in ("doc_id", "text", "document_sha256", "context")}
        cert = answers[raw["doc_id"]]
        requirements = []
        for i, witness in enumerate(cert["joined_evidence"]):
            for section in ("directory", "payment"):
                requirements.append({"id": f"J{i}-{section}", "kind": "body", **witness[section]})
        requirements.extend({"id": "C-" + k, "kind": "context", "pointer": "/" + k,
                             "value_sha256": value_digest(v)} for k, v in raw["context"].items())
        annotation = {"doc_id": raw["doc_id"], "input_sha256": value_digest(raw), "proposed_grade": cert["reference_grade"],
                      "requires_full_body": True, "requirements": requirements}
        output.append((raw, annotation))
    return output


def run(out, ledger_pack, tokenizer_path):
    require(not out.exists(), "fit_output_exists")
    require(not out.resolve().is_relative_to(ledger_pack.resolve()) and
            not out.resolve().is_relative_to(tokenizer_path.parent.resolve()), "fit_output_inside_source")
    require(tokenizer_path.is_file() and tokenizer_path.name == "tokenizer.json", "fit_local_tokenizer_required")
    import tokenizers
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    normalize = load_local_module("_fit_normalizer", "src/koipa/modules/m2_preprocess/normalizer.py").normalize
    splitter = load_local_module("_fit_chunker", "src/koipa/modules/m2_preprocess/chunker.py").split
    source_files = ("scripts/measure_reference_input_fit.py", "scripts/short_reference_probes.py",
                    "src/koipa/modules/m2_preprocess/normalizer.py", "src/koipa/modules/m2_preprocess/chunker.py",
                    "src/koipa/modules/m2_preprocess/pipeline.py", "src/koipa/modules/m5_inference/pipeline.py",
                    "src/koipa/modules/m4_training/trainer.py", "src/koipa/config.py", "src/koipa/policy_facts.py")
    fingerprints = {str(POC / p): hashlib.sha256((POC / p).read_bytes()).hexdigest() for p in source_files}
    fingerprints[str(tokenizer_path.resolve())] = hashlib.sha256(tokenizer_path.read_bytes()).hexdigest()
    inputs, annotations = build_probes()
    require(len(inputs) == len(annotations) == 21, "fit_short_case_count_invalid")
    require(len({r["input"]["doc_id"] for r in inputs}) == 21 and
            len({a["doc_id"] for a in annotations}) == 21, "fit_duplicate_case_id")
    sets = {"short_drafts": [(r["input"], a) for r, a in zip(inputs, annotations, strict=True)], "ledger_fixed": load_ledger(ledger_pack)}
    profiles = ("train_body_first", "serving_body_truncated", "serving_body_overflow", "proposed_body_context_first")
    results, summary = [], {}
    for name, pairs in sets.items():
        by_profile = {}
        for profile in profiles:
            rows = []
            for raw, annotation in pairs:
                detail = inspect_view(raw, annotation, tokenizer, normalize, splitter, profile=profile)
                detail["dataset"] = name
                results.append(detail)
                rows.append({**detail, "grade": annotation["proposed_grade"]})
            by_profile[profile] = {"rows": len(rows), "necessary_inputs_observed": sum(r["all_required_input_observed"] for r in rows),
                "entire_view_one_window": sum(r["single_window_contains_entire_view"] for r in rows),
                "full_offset_coverage": sum(r["full_token_offset_coverage"] for r in rows),
                "full_tokens_min": min(r["full_token_count"] for r in rows), "full_tokens_max": max(r["full_token_count"] for r in rows),
                "windows_min": min(r["windows"] for r in rows), "windows_max": max(r["windows"] for r in rows),
                "rows_with_unk": sum(r["unk_tokens"] > 0 for r in rows), "collision_bound": collision_bound(rows)}
        summary[name] = by_profile
    for path, digest in fingerprints.items():
        require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, "fit_source_changed_during_run")
    verify_pack(ledger_pack)
    report = {**FLAGS, "schema_version": "reference-input-fit-v0.2", "policy_version": VERSION,
              "profiles_are_backend_diagnostics_not_service_execution": True, "normalization": "normalize() default, no PII mask",
              "train_body_first_profile": "first512 of normalized diagnostic text; not a replay of a specific training dataset",
              "max_length": 512, "char_chunk_size": 1536, "char_overlap": 64, "token_overflow_stride": 64,
              "tokenizers_version": tokenizers.__version__, "tokenizer_path": str(tokenizer_path.resolve()),
              "active_server_model_verified": False, "model_weights_loaded": False, "model_forward_executed": False,
              "new_fixed_grades": 0, "short_drafts": {"rows": 21, "source_texts": 9, "genres": 3,
                "conditional_grade_hypotheses": 18, "hold_hypotheses": 3, "blind": False},
              "summary": summary, "source_sha256": fingerprints,
              "ledger_manifest_sha256": hashlib.sha256((ledger_pack / "manifest.json").read_bytes()).hexdigest()}
    payloads = {"inputs.draft.jsonl": _jsonl(inputs), "annotations.draft.jsonl": _jsonl(annotations),
                "visibility.jsonl": _jsonl(results), "report.json": _json(report)}
    for row in inputs:
        raw = row["input"]
        payloads[f"documents/{raw['doc_id']}.md"] = raw["text"]
    out.mkdir(parents=True, exist_ok=False)
    for name, text in payloads.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    with (out / "manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_json({**FLAGS, "schema_version": "reference-input-fit-pack-v0.2",
            "files": {p: text_digest(s) for p, s in payloads.items()}, "source_sha256": fingerprints}))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-pack", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run(args.out, args.ledger_pack, args.tokenizer)
        print(json.dumps({"status": "input_visibility_measured", "short_drafts": report["short_drafts"], "new_fixed_grades": 0}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Stable contract codes are safe; never echo input content from generic exceptions.
        code = str(exc) if type(exc).__name__ == "FactContractError" else "fit_check_failed"
        print(json.dumps({"status": "invalid", "code": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

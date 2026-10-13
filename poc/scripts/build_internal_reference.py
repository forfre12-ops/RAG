"""Build/replay an internal controlled-language pilot. No customer GOLD or training.

Every family has all four labels; proposed labels never enter the certifier.
CLI writes only a NEW directory. Verification rejects empty/tampered packs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from koipa.internal_reference import (FLAGS, GRADES, POLICY, POLICY_SHA256, SECTIONS, TITLES,
                                      audit_policy_table, certify_reference, style_view, verify_certificate)
from koipa.policy_facts import FactContractError, require, text_digest, value_digest

SOURCE_PATHS = ("src/koipa/internal_reference.py", "src/koipa/internal_reference_oracle.py",
                "src/koipa/policy_facts.py", "scripts/build_internal_reference.py",
                "scripts/measure_ngram_shortcuts.py", "docs/INTERNAL_FIXED_REFERENCE_POLICY_V0_1.md")


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_):
    raise FactContractError("nonfinite_json_constant")


def _loads(text):
    return json.loads(text, object_pairs_hook=_object, parse_constant=_constant)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + "\n"


def _jsonl(rows):
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for row in rows)


def render(tables, family):
    """Formatting depends on family, never on label."""
    order = list(SECTIONS)
    rng = random.Random(8700 + family)
    rng.shuffle(order)
    lines = [TITLES[family % len(TITLES)], "기록형식: LEDGER-01"]
    for section in order:
        columns = list(SECTIONS[section])
        rng.shuffle(columns)
        lines.extend((section, "|".join(columns)))
        lines.extend("|".join(row[c] for c in columns) for row in tables[section])
    return "\n".join([*lines, "[끝]"]) + "\n"


def make_input(*, family, linked_people, released, row_count=1200):
    """Create values first. No grade argument and no grade-indicating ID prefix."""
    require(0 <= linked_people <= row_count <= 5000, "generator_count_out_of_range")
    rng = random.Random(2026091500 + family)
    seen = set()

    def key(prefix):
        while True:
            value = prefix + "-" + format(rng.getrandbits(64), "016x")
            if value not in seen:
                seen.add(value)
                return value

    identities = [{"연결키": key("K"), "인물ID": key("P"),
                   "표시명": "가상인물-" + format(rng.getrandbits(32), "08x")} for _ in range(row_count)]
    unlinked = [key("K") for _ in range(row_count)]
    recipients = [r["연결키"] for r in identities[:linked_people]] + unlinked[linked_people:]
    rng.shuffle(recipients)
    payments = [{"처리키": key("T"), "연결키": recipient, "금액단위": f"{rng.randrange(1000, 9000000):010d}"}
                for recipient in recipients]
    allocations = [{"작업키": key("W"), "설비": f"설비-{rng.randrange(30):02d}",
                    "시간단위": f"{rng.randrange(1, 300):04d}"} for _ in range(16 + family)]
    rng.shuffle(identities)
    text = render({"[연결표]": identities, "[지급표]": payments, "[배정표]": allocations}, family)
    context = {"origin": "synthetic_assumption", "scope_complete": True,
               "identity_scope": "supplied_directory_only", "release_authorized": released}
    # Metadata, not a model feature. Even S2/S3 identical bodies get distinct input IDs.
    doc_id = "ir-" + value_digest({"body": text_digest(text), "context": context})[:20]
    return {"schema_version": "internal-fixed-ledger-input-v0.1", "doc_id": doc_id,
            "policy_sha256": POLICY_SHA256, "document_sha256": text_digest(text), "text": text, "context": context}


def pilot_inputs():
    records = []
    for family in range(10):
        counts = {"TS": (1000, 1001, 1100, 1199, 1200)[family % 5],
                  "S1": (1, 2, 200, 998, 999)[family % 5], "S2": 0, "S3": 0}
        for grade in GRADES:
            released = grade == "S3" if grade in ("S2", "S3") else bool(family % 2)
            raw = make_input(family=family, linked_people=counts[grade], released=released)
            records.append({**FLAGS, "input": raw, "family_id": f"ledger-layout-{family:02d}",
                            "partition": "development" if family < 7 else "reserve_candidate",
                            "generation_target": grade})
    random.Random(71103).shuffle(records)
    return records


def validate_rows(inputs, answers):
    require(len(inputs) == len(answers) == 40, "reference_pilot_requires_40_rows")
    by_id = {row["input"]["doc_id"]: row for row in inputs}
    answers_by_id = {row["certificate"]["doc_id"]: row for row in answers}
    require(len(by_id) == len(answers_by_id) == 40 and set(by_id) == set(answers_by_id), "reference_id_mismatch")
    families, styles, bodies, views = defaultdict(list), defaultdict(list), defaultdict(list), set()
    counts, partitions = Counter(), Counter()
    for doc_id, row in by_id.items():
        require(set(row) == {*FLAGS, "input"} and
                value_digest({k: row[k] for k in FLAGS}) == value_digest(FLAGS), "reference_input_envelope_invalid")
        raw, answer = row["input"], answers_by_id[doc_id]
        require(set(answer) == {"certificate", "family_id", "partition", "generation_target"}, "reference_answer_envelope_invalid")
        cert = answer["certificate"]
        verify_certificate(raw, cert)
        require(cert["status"] == "fixed_under_internal_policy", "reference_pilot_contains_hold")
        require(cert["reference_grade"] == answer["generation_target"], "generation_target_disagrees")
        require(answer["family_id"] in {f"ledger-layout-{n:02d}" for n in range(10)}, "reference_family_invalid")
        expected_partition = "development" if int(answer["family_id"].split("-")[-1]) < 7 else "reserve_candidate"
        require(answer["partition"] == expected_partition, "reference_partition_invalid")
        grade = cert["reference_grade"]
        families[answer["family_id"]].append((grade, answer["partition"], len(raw["text"])))
        styles[text_digest(style_view(raw))].append(grade)
        bodies[raw["document_sha256"]].append([doc_id, answer["family_id"], answer["partition"]])
        semantic_hash = value_digest({"text": raw["text"], "context": raw["context"], "policy": raw["policy_sha256"]})
        require(semantic_hash not in views, "duplicate_semantic_input")
        views.add(semantic_hash)
        counts[grade] += 1
        partitions[answer["partition"]] += 1
    require(counts == dict.fromkeys(GRADES, 10) and len(families) == 10, "reference_grade_balance_invalid")
    for members in families.values():
        require(Counter(r[0] for r in members) == dict.fromkeys(GRADES, 1), "reference_family_label_balance_invalid")
        require(len({r[1] for r in members}) == len({r[2] for r in members}) == 1, "reference_family_split_or_length_leak")
    for grades in styles.values():
        require(len(set(Counter(grades).values())) == 1 and set(grades) == set(GRADES), "reference_style_label_imbalance")
    for members in bodies.values():
        require(len({r[1] for r in members}) == len({r[2] for r in members}) == 1, "reference_body_cross_split")
    return {**FLAGS, "status": "validated_internal_policy_only", "counts": dict(counts),
            "partitions": dict(partitions), "families": len(families), "fixed": 40, "hold": 0,
            "independent_sql_agreement": {"numerator": 40, "denominator": 40},
            "style_signatures": len(styles), "style_signatures_balanced": True,
            "unique_bodies": len(bodies), "unique_text_context_inputs": len(views),
            "intentional_within_family_counterfactual_duplicates": [members for members in bodies.values() if len(members) > 1],
            "cross_partition_body_or_family_overlap": 0, "policy_boundary_checks": audit_policy_table(),
            "full_content_shortcut_probe_run": False, "natural_language_generalization_measured": False,
            "existing_training_pool_overlap": "not_measured_in_this_pack_validator"}


def build_pack(out):
    require(not out.exists(), "output_exists")
    inputs, answers = [], []
    for row in pilot_inputs():
        raw = row["input"]
        cert = certify_reference(raw)
        inputs.append({**FLAGS, "input": raw})
        answers.append({"certificate": cert, **{k: row[k] for k in ("family_id", "partition", "generation_target")}})
    validation = validate_rows(inputs, answers)
    sources = {p: hashlib.sha256((POC / p).read_bytes()).hexdigest() for p in SOURCE_PATHS}
    payloads = {"policy.json": _json(POLICY), "inputs.jsonl": _jsonl(inputs), "answers.jsonl": _jsonl(answers),
                "validation.json": _json(validation),
                "README.md": "# 내부 고정 원장 참조 파일럿 v0.1\n\n"
                "40건(등급별10), 10개 서식 계열. 정답은 제공된 본문+가상 맥락+내부 정책에만 유효합니다.\n"
                "고객 GOLD/법정등급/실문서 정확도/학습 허가가 아닙니다. 사람 서명은 생성하지 않았습니다.\n"
                "개발28/예비후보12. 예비후보도 제작·검증자가 열람했으므로 블라인드 봉인 자료가 아닙니다.\n"
                "입력은 inputs.jsonl의 input.text+input.context+policy.json입니다. 답안/ID/계열은 특징으로 쓰지 않습니다.\n"
                "본문만 같은 S2/S3 10쌍은 공개 허가 맥락에 따른 대조쌍이며 분할을 넘지 않습니다.\n"
                "학습/모델성능 분모 사용 금지. 정책 JSON의 1,000명 경계는 내부 설계값입니다.\n"}
    for row in inputs:
        raw = row["input"]
        payloads[f"documents/{raw['doc_id']}.md"] = raw["text"]
    manifest = {**FLAGS, "schema_version": "internal-fixed-ledger-pack-v0.1", "policy_sha256": POLICY_SHA256,
                "source_files_sha256": sources, "generator_seed": 2026091500,
                "files": {name: text_digest(payload) for name, payload in payloads.items()}}
    out.mkdir(parents=True, exist_ok=False)
    for name, payload in {**payloads, "manifest.json": _json(manifest)}.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    return verify_pack(out)


def verify_pack(out):
    root = out.resolve()
    manifest_bytes = (root / "manifest.json").read_bytes()
    manifest = _loads(manifest_bytes.decode("utf-8"))
    require(manifest["schema_version"] == "internal-fixed-ledger-pack-v0.1" and
            manifest["policy_sha256"] == POLICY_SHA256 and
            value_digest({k: manifest[k] for k in FLAGS}) == value_digest(FLAGS), "reference_manifest_invalid")
    require(set(manifest["source_files_sha256"]) == set(SOURCE_PATHS), "reference_source_list_invalid")
    for name, digest in manifest["source_files_sha256"].items():
        require(hashlib.sha256((POC / name).read_bytes()).hexdigest() == digest, "reference_source_drift")
    data = {}
    for name, digest in manifest["files"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and path != root and "\\" not in name and not Path(name).is_absolute(), "reference_manifest_path_escape")
        payload = path.read_bytes()
        require(hashlib.sha256(payload).hexdigest() == digest, "reference_pack_hash_mismatch")
        data[name] = payload
    required = {"policy.json", "inputs.jsonl", "answers.jsonl", "validation.json", "README.md"}
    require(required <= set(data), "reference_pack_missing_file")
    require(_loads(data["policy.json"].decode("utf-8")) == POLICY, "reference_policy_file_mismatch")
    inputs = [_loads(line) for line in data["inputs.jsonl"].decode("utf-8").splitlines()]
    answers = [_loads(line) for line in data["answers.jsonl"].decode("utf-8").splitlines()]
    validation = validate_rows(inputs, answers)
    docs = {f"documents/{row['input']['doc_id']}.md": row["input"]["text"] for row in inputs}
    require(set(data) == required | set(docs), "reference_pack_unexpected_file")
    for name, text in docs.items():
        require(data[name].decode("utf-8") == text, "reference_document_export_mismatch")
    require(_loads(data["validation.json"].decode("utf-8")) == validation, "reference_validation_report_mismatch")
    require({str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()} == set(data) | {"manifest.json"}, "reference_pack_unlisted_file")
    require((root / "manifest.json").read_bytes() == manifest_bytes, "reference_manifest_changed_during_check")
    for name, payload in data.items():
        require((root / name).read_bytes() == payload, "reference_file_changed_during_check")
    return validation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--build", type=Path)
    action.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    try:
        result = build_pack(args.build) if args.build else verify_pack(args.verify)
        print(json.dumps({k: result[k] for k in ("status", "counts", "fixed", "hold", "partitions")}, ensure_ascii=False))
        return 0
    except (FactContractError, OSError, ValueError, KeyError, TypeError, IndexError):
        # Do not echo document content or local file contents on malformed input.
        print(json.dumps({"status": "invalid", "code": "internal_reference_check_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

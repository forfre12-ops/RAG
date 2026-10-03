"""Serialize the AI-authored development90 + legacy4 reading notes, offline.

Only development case bodies and four named historical bodies are reviewed.
Sealed files are hashed for preservation, not parsed for semantic review.
No inference, training, original-label change, signature or approval is performed.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from content_reference_contract import POLICY_ID, require, text_hash
from content_reference_review_notes import DEV_NOTES, LEGACY_NOTES
from evaluation_inputs import read_rows, sha256, text_of
from prepare_content_reference import write_json, write_jsonl

POC = Path(__file__).resolve().parents[1]
PACK = POC / "reports/CONTENT_REFERENCE_20260914/reference_v1"
POLICY = POC / "docs/CONTENT_PROTECTION_REFERENCE_V1.md"
ISSUES = POC / "docs/CONTENT_REFERENCE_POLICY_ISSUES_2026-09-15.md"
MANIFEST_SHA = "ee871d45cb7a5239a0bde1f29a4589e6a29b73b2faf7edf8637e41835bbe7079"
POLICY_SHA = "4263c96b624f26ea14f1eed919516a01e38f896dcc5990fc6e1dbcc3c120f66f"
NAMES = {"keep": "유지", "amend_material": "수정 후보(본문·용도 보완)",
         "needs_context": "추가 확인", "propose_under_reference": "새 기준 해석 제안"}


def record_sha(row: dict) -> str:
    return text_hash(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def evidence(body: str, quote: str, origin: str) -> dict:
    require(bool(quote) and body.count(quote) == 1, "Quote must resolve to exactly one body span")
    start = body.index(quote)
    return {"start": start, "end": start + len(quote), "quote": quote, "origin": origin,
            "indexing": "Python codepoints [start,end); not byte offsets"}


def source_inputs(pack: Path = PACK, root: Path = POC) -> tuple:
    require(sha256(pack / "manifest.json") == MANIFEST_SHA and sha256(POLICY) == POLICY_SHA,
            "Review notes are pinned to the original pack/policy; do not reuse after a version change")
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    protected = {pack / "manifest.json": MANIFEST_SHA, POLICY: POLICY_SHA}
    for item in manifest["files"]:
        path = (pack / item["path"]).resolve()
        require(path.is_relative_to(pack.resolve()), "Pack path escapes input scope")
        require(sha256(path) == item["sha256"], "Frozen reference file changed")
        protected[path] = item["sha256"]
    inputs = read_rows(pack / "development/inputs.jsonl")
    answers = read_rows(pack / "development/answers.candidate.jsonl")
    require(len(inputs) == len(answers) == 90, "Expected development90 only")
    require([r["doc_id"] for r in inputs] == manifest["splits"]["development"], "Development order/IDs changed")
    require(set(manifest["splits"]["development"]).isdisjoint(manifest["splits"]["sealed_candidate"]), "Split leakage")
    docs, answer_map = {d["doc_id"]: d for d in inputs}, {a["doc_id"]: a for a in answers}
    require(len(docs) == len(answer_map) == 90 and set(docs) == set(answer_map), "Duplicate/mismatched development IDs")
    for d in inputs:
        a = answer_map[d["doc_id"]]
        require(d["text_sha256"] == a["text_sha256"] == text_hash(d["text"]), "Source body binding mismatch")
        require(d["policy_version"] == a["policy_version"] == POLICY_ID and
                d["policy_sha256"] == a["policy_sha256"] == POLICY_SHA, "Source policy binding mismatch")
    # The historical issue index pins both source files, IDs and unchanged bodies.
    historical = []
    for issue in json.loads((pack / "legacy_label_issues.json").read_text(encoding="utf-8")):
        sides = []
        for side in issue["sides"]:
            path = (root / side["path"]).resolve()
            require(path.is_relative_to(root.resolve()) and sha256(path) == side["file_sha256"], "Historical source changed")
            protected[path] = side["file_sha256"]
            matched = [r for r in read_rows(path) if r.get("doc_id") == issue["doc_id"]]
            require(len(matched) == 1, "Historical ID not unique")
            row = matched[0]
            require(text_hash(text_of(row)) == side["text_sha256"] and row["label"] == side["label"], "Historical body/label mismatch")
            sides.append((side, row))
        require(text_of(sides[0][1]) == text_of(sides[1][1]), "Historical bodies differ")
        historical.append((issue["doc_id"], sides))
    require(len(historical) == 4, "Expected four historical disputes")
    return docs, answer_map, historical, protected


def review_common(doc_id, body, scope) -> dict:
    return {"review_id": f"CPR-{doc_id}", "doc_id": doc_id, "scope": scope,
            "text_sha256": text_hash(body), "reference_policy": {"version": POLICY_ID, "sha256": POLICY_SHA},
            "review_origin": "same_ai_author_reinspection", "independent_human_review": False,
            "approval_status": "unapproved", "human_signature": None,
            "gold_qualified": False, "training_allowed": False, "original_label_replacement": None}


def development_reviews(docs: dict, answers: dict) -> list[dict]:
    ids = [n[0] for n in DEV_NOTES]
    require(len(ids) == len(set(ids)) == 90 and set(ids) == set(docs), "Authored review coverage mismatch")
    rows = []
    for doc_id, expected, disposition, quote, finding, action in DEV_NOTES:
        doc, original = docs[doc_id], answers[doc_id]
        require(expected == (original["expected_grade"] or original["expected_status"]), "Authored note/source answer mismatch")
        issues = ["CPR-I02", "CPR-I10"]
        if disposition == "amend_material":
            issues.append("CPR-I01")
        if "CP-TS-03" in original["rule_ids"]:
            issues.append("CPR-I03")
        if "CP-TS-02" in original["rule_ids"]:
            issues.append("CPR-I04")
        if expected == "S1":
            issues.append("CPR-I05")
        if expected == "S2":
            issues.append("CPR-I07")
        if disposition == "needs_context":
            issues.append("CPR-I08")
        if original["authoring_source"]["catalog_key"] == "biometric_count":
            issues.extend(["CPR-I03", "CPR-I06"])
        rows.append(review_common(doc_id, doc["text"], "development_candidate") | {
            "family_id": doc["family_id"], "topic": doc["source"]["topic"],
            "source_path": "development/inputs.jsonl", "source_record_sha256": record_sha(doc),
            "source_answer_path": "development/answers.candidate.jsonl", "source_answer_sha256": record_sha(original),
            "source_answer": {"grade": original["expected_grade"], "status": original["expected_status"],
                              "rule_ids": original["rule_ids"], "candidate_grades": original["candidate_grades"]},
            "disposition": disposition, "disposition_ko": NAMES[disposition],
            "proposal": {"grade": original["expected_grade"], "status": original["expected_status"],
                         "scope": "original_fictional_conditions_only", "grade_change_proposed": False},
            "rule_ids": original["rule_ids"], "evidence": [evidence(doc["text"], quote, "fictional_scenario_text")],
            "finding": finding, "next_action": action, "policy_issue_ids": sorted(set(issues)),
            "real_document_evaluation_ready": False,
            "candidate_bounds_note": "가상 조건에 한정된 범위이며 실제 미수신 문서의 위험 상한이 아님" if not original["expected_grade"] else None,
        })
    return rows


def legacy_reviews(historical: list) -> list[dict]:
    sources = dict(historical)
    require({n["doc_id"] for n in LEGACY_NOTES} == set(sources), "Historical note coverage mismatch")
    result = []
    for note in LEGACY_NOTES:
        doc_id = note["doc_id"]
        sides = sources[doc_id]
        body = text_of(sides[0][1])
        result.append(review_common(doc_id, body, "legacy_conflict") | {
            "source_sides": [side | {"record_sha256": record_sha(row)} for side, row in sides],
            "disposition": note["disposition"], "disposition_ko": NAMES[note["disposition"]],
            "proposal": {"grade": note["proposal_grade"], "status": note["proposal_status"],
                         "scope": "new_reference_interpretation_not_legacy_truth", "changes_original": False},
            "rule_ids": note["rule_ids"], "evidence": [evidence(body, q, "existing_public_scenario_text") for q in note["quotes"]],
            "finding": note["finding"], "not_higher_reason": note["not_higher"], "not_lower_reason": note["not_lower"],
            "next_action": note["next_action"], "policy_issue_ids": note["issue_ids"],
            "historical_policy_version_verified": False, "historical_legal_reference_verified": False,
        })
    return result


def validate_reviews(dev: list, legacy: list, docs: dict, historical: list, issue_doc: Path = ISSUES) -> dict:
    require(len(dev) == 90 and len(legacy) == 4, "Review count mismatch")
    require({r["doc_id"] for r in dev} == set(docs), "Development review IDs mismatch")
    legacy_docs = {key: text_of(sides[0][1]) for key, sides in historical}
    bodies = {key: row["text"] for key, row in docs.items()} | legacy_docs
    require({r["doc_id"] for r in legacy} == set(legacy_docs), "Historical review IDs mismatch")
    require(len({r["review_id"] for r in dev + legacy}) == 94, "Duplicate review IDs")
    issues = issue_doc.read_text(encoding="utf-8")
    evidence_n = 0
    for row in dev + legacy:
        body = bodies[row["doc_id"]]
        require(row["text_sha256"] == text_hash(body), "Review body hash mismatch")
        require(row["reference_policy"] == {"version": POLICY_ID, "sha256": POLICY_SHA}, "Review policy mismatch")
        require(row["approval_status"] == "unapproved" and row["human_signature"] is None and
                row["gold_qualified"] is False and row["training_allowed"] is False and
                row["independent_human_review"] is False and row["original_label_replacement"] is None,
                "Review cannot fabricate authority or replace original labels")
        require(row["disposition"] in NAMES and bool(row["finding"]) and bool(row["next_action"]), "Missing review decision")
        require(bool(row["policy_issue_ids"]) and all(f"## {key} —" in issues for key in row["policy_issue_ids"]), "Unbound policy issue")
        require(bool(row["evidence"]), "Missing exact evidence")
        for e in row["evidence"]:
            require(type(e["start"]) is int and type(e["end"]) is int and 0 <= e["start"] < e["end"] <= len(body)
                    and body[e["start"]:e["end"]] == e["quote"], "Invalid review evidence span")
            evidence_n += 1
        if row["scope"] == "development_candidate":
            require(row["proposal"]["grade"] == row["source_answer"]["grade"] and
                    row["proposal"]["status"] == row["source_answer"]["status"] and
                    row["proposal"]["grade_change_proposed"] is False, "Do not turn material fixes into grade changes")
        if row["disposition"] == "needs_context":
            require(row["proposal"]["grade"] is None, "Context uncertainty must not become a confirmed grade")
    return {"status": "REVIEW_BINDING_CHECKS_OK", "development_reviewed": len(dev), "legacy_reviewed": len(legacy),
            "development_dispositions": dict(Counter(r["disposition"] for r in dev)),
            "legacy_dispositions": dict(Counter(r["disposition"] for r in legacy)), "exact_evidence_spans": evidence_n,
            "development_grade_changes_proposed": 0, "original_labels_changed": 0, "policy_changed": False,
            "sealed_content_semantically_reviewed": False, "model_executed": False,
            "independent_human_review": False, "model_accuracy_measured": False,
            "policy_issue_count": 10, "issue_affected_review_counts": dict(Counter(k for r in dev + legacy for k in r["policy_issue_ids"]))}


def render(rows: list[dict], title: str) -> str:
    lines = [f"# {title}", "", "AI 재검토 제안·미승인. 원본 라벨/정책 변경, 독립 사람 검수, 모델 품질 측정은 아니다.", "",
             "‘수정 후보’는 본문·용도 보완이며 개발 후보의 등급을 바꾸었다는 뜻이 아니다.", "",
             "| 사례 ID | 기존 답/라벨 | 검토 | 제안 |", "|---|---|---|---|"]
    for r in rows:
        old = str(r["source_answer"]["grade"] or r["source_answer"]["status"]) if "source_answer" in r else "/".join(s["label"] for s in r["source_sides"])
        lines.append(f"| {r['doc_id']} | {old} | {r['disposition_ko']} | {r['proposal']['grade'] or r['proposal']['status']} |")
    lines.append("")
    for r in rows:
        lines.extend([f"## {r['doc_id']}", "", f"검토: **{r['disposition_ko']}** / 적용 규칙: {', '.join(r['rule_ids'])}", "",
                      r["finding"], "", "다음 조치: " + r["next_action"], "",
                      "정책 쟁점: " + ", ".join(r["policy_issue_ids"]), ""])
        for e in r["evidence"]:
            lines.extend([f"> {e['quote']}", "", f"원문 문자 `[{e['start']},{e['end']})` / 본문 SHA-256 `{r['text_sha256']}`", ""])
        if "source_sides" in r:
            lines.extend(["상위 배제: " + r["not_higher_reason"], "", "하위 확정 제한: " + r["not_lower_reason"], "",
                          "출처: " + ", ".join(s["path"] for s in r["source_sides"]), "",
                          "옛 정책·법률 참조 진위는 미확인. 새 기준 해석을 옛 정답의 확정으로 사용하지 않는다.", ""])
    return "\n".join(lines)


def build(out: Path, pack: Path = PACK) -> dict:
    require(not out.exists(), "Output exists; choose a NEW review directory")
    require(not out.resolve().is_relative_to(pack.resolve()), "Never write inside the frozen candidate pack")
    docs, answers, historical, protected = source_inputs(pack)
    dev, legacy = development_reviews(docs, answers), legacy_reviews(historical)
    summary = validate_reviews(dev, legacy, docs, historical)
    require(all(sha256(p) == digest for p, digest in protected.items()), "Source changed during review serialization")
    out.mkdir(parents=True, exist_ok=False)
    write_jsonl(out / "development_review90.jsonl", dev)
    write_jsonl(out / "legacy_review4.jsonl", legacy)
    for name, rows, title in (("DEVELOPMENT_REVIEW90.md", dev, "개발90건 내용 검수표"),
                              ("LEGACY_REVIEW4.md", legacy, "기존 라벨 충돌4건 내용 검수")):
        with (out / name).open("x", encoding="utf-8", newline="\n") as fh:
            fh.write(render(rows, title))
    write_json(out / "summary.json", summary)
    write_json(out / "preservation.json", {"status": "PRESERVED", "files": [
        {"path": str(p.relative_to(POC)) if p.is_relative_to(POC) else str(p), "sha256": digest} for p, digest in protected.items()],
        "checked_files": len(protected), "sealed_files_hashed_only": True})
    sources = [Path(__file__), Path(__file__).with_name("content_reference_review_notes.py"), ISSUES,
               Path(__file__).with_name("content_reference_contract.py"), Path(__file__).with_name("evaluation_inputs.py"),
               Path(__file__).with_name("prepare_content_reference.py")]
    manifest = {"schema_version": "content-reference-review-v1", "created_at": datetime.now(timezone.utc).isoformat(),
                "review_origin": "same_ai_author_reinspection", "approval_status": "unapproved", "training_allowed": False,
                "source_pack_manifest_sha256": MANIFEST_SHA, "reference_policy_sha256": POLICY_SHA,
                "source_files": [{"path": p.relative_to(POC).as_posix(), "sha256": sha256(p)} for p in sources],
                "files": [{"path": p.relative_to(out).as_posix(), "sha256": sha256(p)} for p in sorted(out.iterdir()) if p.is_file()]}
    write_json(out / "manifest.json", manifest)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = build(POC / args.out)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

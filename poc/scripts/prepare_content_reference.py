"""Build a version-bound, unapproved 140-case reference pack. Offline only.

Usage (from poc): python scripts/prepare_content_reference.py --out reports/NEW_PACK
Refuses existing output paths. Does not export training data or run inference.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from content_reference_cases import FAMILIES, REVIEW_FAMILIES
from content_reference_contract import (
    GRADES, POLICY_ID, PREDICATES, RULE_GRADES, RULES, known_pool_overlap,
    require, text_hash, validate_pack,
)
from evaluation_inputs import normalized_hash, read_rows, sha256, text_of

POC = Path(__file__).resolve().parents[1]
POLICY = POC / "docs/CONTENT_PROTECTION_REFERENCE_V1.md"
SEED = 20260914
KNOWN_POOLS = ["datasets/labeled_p1_v5_clean/train.jsonl", "datasets/labeled_p1_v5_clean/val.jsonl",
               "datasets/labeled_p1_v5_clean/test.jsonl", "datasets/gold_real/train_subset.jsonl"]
CONFLICT_BOUNDS = {
    "missing_recipe": ["TS", "S3"], "key_validity": ["TS", "S3"], "population": ["TS", "S1"],
    "source_package": ["TS", "S3"], "linkability": ["S1", "S2"], "price_status": ["S1", "S2"],
    "filled_form": ["S1", "S3"], "release_scope": ["S2", "S3"],
    "biometric_count": ["TS", "S3"], "technical_appendix": ["TS", "S3"],
}
EXPLANATION = {
    "core_package": "미공개 핵심 대상의 단계·변수·검증 조건과 완전 재현 가능성을 사례 안에서 명시했다.",
    "live_control": "가상 세계에서 현재 유효하고 핵심 통제를 가능하게 하는 재료 원문과 권한을 명시했다.",
    "bulk_sensitive": "1,000명 이상인 식별 가능한 개인과 민감 세부정보의 연결을 사례 안에서 명시했다.",
    "specific": "특정 개인·거래·제품의 실질적인 보호 세부내용이 존재하고 TS 경계에는 이르지 않는다.",
    "internal": "현재 조직별 업무 일정·역할·작업 식별자는 남아 있지만 TS/S1 보호 세부값은 없다.",
    "generic": "일반 설명이나 값이 비어 있는 공통 서식으로, 상위 보호 대상 내용을 담지 않는 조건이다.",
}
NOT_HIGHER = {
    "TS": "정책 내 최상위 등급이며 상위 등급은 정의하지 않았다. 실제 피해 상한을 뜻하지 않는다.",
    "S1": "완전한 핵심 재현 패키지·유효 통제 재료·1,000명 이상 결합 민감 원장은 사례 범위에 없다. 부분 또는 소규모 보호 정보만으로 TS를 부여하지 않는다.",
    "S2": "사례 범위에 TS의 세 위험이나 S1의 개인·거래·실질 기술 세부값이 없다. 내부 제목만으로 S1/TS를 추정하지 않는다.",
    "S3": "사례 범위에 재현·통제·대량 민감정보, 특정 보호 세부값, 현재 내부 운영 세부사항이 없다. 서식 항목명과 예시 보안표시는 실제 값이 아니다.",
}
NOT_LOWER = {
    "TS": "입증된 TS 조건을 S1 이하의 부분·일반 보호로 축소할 수 없다.",
    "S1": "본문의 특정 보호 세부값을 일반 내부 일정(S2)이나 일반 자료(S3)로 취급할 수 없다.",
    "S2": "현재 내부 업무 식별자·역할·일정이 실제로 남아 있으므로 일반 공통 자료(S3)가 아니다.",
    "S3": "정책 내 최하위 등급이다. 외부 전송이나 재사용 권한까지 부여하는 판단은 아니다.",
}


def make_case(key: str, topic: str, variant: int, body: str, policy_sha: str,
              *, grade: str | None, selected: str, lower: str | None = None, conflict=False) -> tuple[dict, dict]:
    # Expected answers are specified by the authored catalog, never resolve().
    family_id = "CF-" + text_hash(key)[:12]
    doc_id = "CR-" + text_hash(f"{key}:{variant}")[:16]
    source = {"kind": "synthetic_scenario", "real_document": False, "author_type": "AI",
              "topic": topic, "catalog": "scripts/content_reference_cases.py",
              "representation": "fictional_scenario_brief_not_full_length_customer_document",
              "scope": "기재한 내용만 존재한다는 가상 조건. 대량 원장은 실제 행을 생성하지 않고 존재·인원·필드 조건으로 서술한다.",
              "completeness": "complete_scenario" if grade else "scenario_with_explicit_missing_or_conflicting_context"}
    binding = {"doc_id": doc_id, "family_id": family_id, "text_sha256": text_hash(body),
               "policy_version": POLICY_ID, "policy_sha256": policy_sha}
    doc = binding | {"text": body, "source": source}
    values = dict.fromkeys(PREDICATES, False)
    if grade:
        values[selected] = True
        # TS may contain S1-type information as well; highest applicable grade wins.
        if selected in {"core_package", "bulk_sensitive", "live_control"}:
            values["specific"] = True
        status, rules, candidates = "recommended", [RULES[selected]], [grade]
    else:
        values[selected] = None
        values[lower] = True
        status = "needs_policy_review" if conflict else "needs_evidence"
        rules = ["CP-HOLD-02" if conflict else "CP-HOLD-01"]
        candidates = CONFLICT_BOUNDS[key] if conflict else [RULE_GRADES[selected], RULE_GRADES[lower]]
        if conflict:
            # The context itself is disputed. Do not simultaneously assert the
            # lower interpretation as known while listing S3 as an alternative.
            values = dict.fromkeys(PREDICATES, None)
    facts = {k: {"state": "unknown" if v is None else "known", "value": v,
                 "origin": "synthetic_author_assumption", "evidence_ids": ["E1"],
                 "scope_note": ("본문이 확인 불가라고 명시한 조건; false로 대체하지 않음." if v is None else
                                "가상 사례 전체 범위의 긍정 조건." if v else
                                "기재한 내용 외에는 없다는 합성 작성 가정. 실제 문서에서 키워드 부재를 증명한 것이 아님.")}
             for k, v in values.items()}
    rationale = f"{topic}: {EXPLANATION[selected]} 근거 E1의 해당 가상 조건에 한정한다." if grade else (
        f"{topic}: 동일 버전·범위의 맥락이 양립하지 않는다. 두 해석 {candidates} 중 하나를 임의 선택하지 않는다."
        if conflict else f"{topic}: {selected}의 성립 여부가 본문에서 미확인이다. {candidates} 경계를 바꿀 정보를 먼저 확보한다.")
    answer = binding | {
        "expected_grade": grade, "expected_status": status, "candidate_grades": candidates,
        "rule_ids": rules, "facts": facts, "context_conflict": conflict,
        "conflict_candidates": candidates if conflict else [],
        "evidence": [{"id": "E1", "start": 0, "end": len(body), "quote": body,
                      "origin": "synthetic_scenario", "location": "가상 사례 본문 전체(짧은 상황 카드)",
                      "scope": "positive text/context plus explicitly declared author closed-world assumptions"}],
        "rationale": rationale,
        "not_higher_reason": NOT_HIGHER[grade] if grade else "상향 해석을 성립시키는 누락 정보 또는 충돌 해소가 없어 상위 등급을 확정하지 않는다.",
        "not_lower_reason": NOT_LOWER[grade] if grade else "미확인을 부정으로 대체하거나 낮은 쪽 기록을 선택해 하향 확정하지 않는다.",
        "missing_evidence": [] if grade or conflict else [f"{topic}: E1에서 누락·미확인으로 명시한 원문/부록/유효성/대상 범위 확인"],
        "conflict_note": body if conflict else None,
        "resolution_owner_role": "문서 소유자와 해당 기준 승인 책임자" if not grade else None,
        "label_source": "generator_intended_label", "answer_author": "AI",
        "authoring_source": {"catalog_key": key, "variant_index": variant},
        "approval_status": "unapproved", "review_status": "pending", "human_signature": None,
        "gold_eligible": False, "training_allowed": False,
    }
    return doc, answer


def build_cases(policy_sha: str) -> tuple[list, list, dict]:
    rng = random.Random(SEED)
    grade_keys, review_keys = [f[0] for f in FAMILIES], [f[0] for f in REVIEW_FAMILIES]
    require(len(set(grade_keys)) == 30 and len(set(review_keys)) == 10 and not set(grade_keys) & set(review_keys),
            "Catalog family counts/IDs invalid")
    rng.shuffle(grade_keys)
    rng.shuffle(review_keys)
    dev = set(grade_keys[:20] + review_keys[:5])
    inputs, answers = [], []
    splits = {"development": [], "sealed_candidate": []}
    for key, topic, ts_predicate, bodies in FAMILIES:
        require(len(bodies) == 4, "Grade family must have four variants")
        for v, (grade, selected, body) in enumerate(zip(GRADES, (ts_predicate, "specific", "internal", "generic"), bodies)):
            doc, answer = make_case(key, topic, v, body, policy_sha, grade=grade, selected=selected)
            inputs.append(doc)
            answers.append(answer)
            splits["development" if key in dev else "sealed_candidate"].append(doc["doc_id"])
    for key, topic, selected, lower, bodies in REVIEW_FAMILIES:
        require(len(bodies) == 2, "Review family must have two variants")
        for v, body in enumerate(bodies):
            doc, answer = make_case(key, topic, v, body, policy_sha, grade=None, selected=selected,
                                    lower=lower, conflict=v == 1)
            inputs.append(doc)
            answers.append(answer)
            splits["development" if key in dev else "sealed_candidate"].append(doc["doc_id"])
    for ids in splits.values():
        rng.shuffle(ids)
    return inputs, answers, splits


def legacy_conflicts(root: Path) -> list[dict]:
    paths = ["datasets/gold_real/holdout_eval.jsonl", "datasets/gold_real/holdout_eval.hardened.jsonl"]
    pools = [{str(r.get("doc_id", r.get("id"))): r for r in read_rows(root / p)} for p in paths]
    result = []
    for doc_id in ("a1beb524ceafe108", "823545b7edf3a0ef", "9a4ace0da18602c1", "6ea073680b55d1e9"):
        left, right = pools[0][doc_id], pools[1][doc_id]
        require(normalized_hash(text_of(left)) == normalized_hash(text_of(right)), "Legacy shared body changed")
        require(left["label"] != right["label"], "Legacy discrepancy no longer present; re-audit before publishing")
        sides = []
        for path, row in zip(paths, (left, right)):
            sides.append({"path": path, "file_sha256": sha256(root / path), "doc_id": doc_id,
                          "text_sha256": text_hash(text_of(row)), "label": row["label"],
                          "policy_version": row.get("policy_version"), "label_source": row.get("label_source"),
                          "label_provenance": row.get("label_provenance"),
                          "evidence_reason_labels": [e.get("reason") for e in row.get("evidence_spans", [])],
                          "eval_truth_level": row.get("eval_truth_level")})
        result.append({"issue_id": "LEGACY-" + doc_id, "doc_id": doc_id, "status": "open_not_adjudicated",
                       "sides": sides, "replacement_label": None, "policy_scope": "legacy_not_this_reference",
                       "analysis": ("원본은 S2, 재심판은 S1. 구체 보호값과 일반 내부운영의 경계를 정책으로 재확인해야 한다."
                                    if left["label"] == "S2" else
                                    "원본은 S1, 재심판은 TS. 일부 코드인지 완전 재현 묶음인지 적용 맥락을 재확인해야 한다."),
                       "evidence_warning": "재심판 라벨과 달리 기존 evidence reason에 이전 라벨이 남아 있을 수 있다. 모델 합의는 사람 서명이 아니다.",
                       "required_resolution": ["두 판단의 적용 정책/버전 확인", "원문 범위와 실제 근거 대조", "근거 sidecar 동시 갱신", "권한 있는 검수자의 판단 기록"],
                       "raw_body_copied": False})
    return result


def comparison_contract() -> dict:
    return {
        "schema_version": "paired-label-comparison-plan-v1", "status": "PREPARED_NOT_EXECUTED",
        "training_enabled": False, "policy_version": POLICY_ID,
        "experiment_ab": {"only_changed_variable": "label", "document_set": "same approved IDs and body hashes in A/B",
                          "arm_a": "original labels", "arm_b": "reviewed content-protection labels",
                          "unresolved_labels": "exclude identical IDs from both arms; publish counts/distribution",
                          "trainer_input": {"text": "unchanged body", "label": "TS|S1|S2|S3"},
                          "sidecar_join": ["doc_id", "text_sha256"],
                          "sidecar_fields": ["old_label", "new_label", "policy_version", "policy_sha256", "rules", "evidence", "review_decision"]},
        "fixed_controls": ["initial model files and exact hashes", "same tokenizer/revision",
                           "same document-family split", "same random seed per paired run",
                           "same max_seq_len/chunking/batch/epochs/lr/weights/early stopping",
                           "same validation population", "same hardware/runtime", "same postprocessing/thresholds"],
        "initial_model_artifact": None, "complete_training_manifest": None, "training_settings_sha256": None,
        "seed_pairs_proposal": [42, 43, 44],
        "data_addition_experiment": "Separate C/D experiment after label-only A/B; no combined effect claim",
        "excluded_inputs": ["this 140-case reference pack", "existing calibration20", "locked_gold_eval", "held_review"],
        "required_before_execution": ["approved rubric and reviewed labels", "complete version-bound training lineage",
                                      "same source bodies and family split", "pinned initial model and full training configuration",
                                      "frozen independent evaluation references", "approved scope for actual training"],
        "promotion": "never automatic; reference-candidate diagnostic is not customer acceptance",
    }


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def casebook(docs: list[dict], answer_map: dict, *, answers: bool) -> str:
    lines = ["# 내용 보호 참조 사례 — " + ("AI 답안 후보집" if answers else "독립 검토 입력집"), "",
             "미승인 합성 상황 카드. 고객사 실문서·GOLD가 아니며 학습에 넣지 않는다.", "",
             "본문의 조건만으로 해당 가상 세계를 판정한다. 인원 수는 실제 행 목록이 아니라 사례 조건이다.", "",
             "입력집에 답안은 없지만 작성자가 양쪽을 알고 있으므로 독립 봉인 완료를 뜻하지 않는다.", ""]
    for doc in docs:
        lines += [f"## {doc['doc_id']} — {doc['source']['topic']}", "",
                  f"계열 `{doc['family_id']}` / 본문 SHA-256 `{doc['text_sha256']}`", "", doc["text"], ""]
        if answers:
            a = answer_map[doc["doc_id"]]
            lines += [f"- 답안 후보: **{a['expected_grade'] or a['expected_status']}**; 후보 범위: {', '.join(a['candidate_grades'])}",
                      f"- 적용 규칙: {', '.join(a['rule_ids'])}", f"- 판단: {a['rationale']}",
                      f"- 상위 배제: {a['not_higher_reason']}", f"- 하위 배제: {a['not_lower_reason']}",
                      f"- 근거: E1, 본문 문자 `[0,{len(doc['text'])})`; 가상 작성 가정이며 실제 증거 인증이 아님.",
                      f"- 정책: `{POLICY_ID}` / SHA-256 `{doc['policy_sha256']}`", "- 서명 없음 / 검토 대기 / GOLD 아님", ""]
            if a["missing_evidence"]:
                lines += ["추가 확인: " + "; ".join(a["missing_evidence"]), ""]
            if a["conflict_note"]:
                lines += ["충돌 해결: 문서 소유자와 기준 승인 책임자가 해당 버전·범위를 대조해야 한다.", ""]
    return "\n".join(lines)


def build(out: Path, root: Path = POC, policy: Path = POLICY) -> dict:
    require(not out.exists(), "Output exists; choose a NEW directory, never overwrite a reference pack")
    inputs, answers, splits = build_cases(sha256(policy))
    validation = validate_pack(inputs, answers, splits, policy)
    validation["known_pool_overlap"] = known_pool_overlap(inputs, root, KNOWN_POOLS)
    conflicts = legacy_conflicts(root)
    docs, answer_map = {d["doc_id"]: d for d in inputs}, {a["doc_id"]: a for a in answers}
    out.mkdir(parents=True, exist_ok=False)
    for split, ids in splits.items():
        ordered = [docs[i] for i in ids]
        write_jsonl(out / split / "inputs.jsonl", ordered)
        write_jsonl(out / split / "answers.candidate.jsonl", [answer_map[i] for i in ids])
        for include, name in ((False, "REVIEW_INPUTS.md"), (True, "ANSWERS_CANDIDATE.md")):
            with (out / split / name).open("x", encoding="utf-8", newline="\n") as fh:
                fh.write(casebook(ordered, answer_map, answers=include))
    write_json(out / "validation.json", validation)
    write_json(out / "legacy_label_issues.json", conflicts)
    write_json(out / "comparison_training_contract.json", comparison_contract())
    issue_lines = ["# 기존 라벨 4건 쟁점 — 해결 전", "", "원문을 복사하지 않고 출처·해시·판단 이력을 보존했다.", ""]
    for issue in conflicts:
        left, right = issue["sides"]
        issue_lines += [f"## {issue['doc_id']}", "", f"`{left['path']}`: {left['label']} / `{right['path']}`: {right['label']}", "",
                        issue["analysis"], "", "재심판 기록(출처 메타데이터): `" + json.dumps(right["label_provenance"], ensure_ascii=False) + "`", "",
                        issue["evidence_warning"], "", "양쪽 정책 버전: 미기재. 대체 라벨: 미확정. 원본 유지.", ""]
    with (out / "LEGACY_LABEL_ISSUES.md").open("x", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(issue_lines))
    readme = f"""# 내용 기반 보호등급 참조 후보 v1

상태: 미승인 AI 답안 후보. **140건 제작·구조검증 완료, 모델 품질 측정/학습/승인 없음.**

정책: `{POLICY_ID}` / SHA-256 `{sha256(policy)}`

| 분할 | TS | S1 | S2 | S3 | 검토 | 합계 |
|---|---:|---:|---:|---:|---:|---:|
| 개발 | 20 | 20 | 20 | 20 | 10 | 90 |
| 봉인 후보 | 10 | 10 | 10 | 10 | 10 | 50 |

- [개발 답안 90건](development/ANSWERS_CANDIDATE.md), [개발 입력집](development/REVIEW_INPUTS.md)
- [봉인 후보 답안 50건](sealed_candidate/ANSWERS_CANDIDATE.md), [봉인 후보 입력집](sealed_candidate/REVIEW_INPUTS.md)
- [검증 결과](validation.json), [기존 라벨 쟁점 4건](LEGACY_LABEL_ISSUES.md)
- [비교 학습 준비 계약](comparison_training_contract.json), [파일 지문](manifest.json)

각 JSONL 입력은 text와 식별/출처/정책 바인딩만 담는다. 모델에는 text만 전달한다.
같은 ID의 answers.candidate.jsonl은 답안·조건·규칙·근거·경계 설명을 담는 별도 sidecar다.
문서 계열의 모든 변형을 같은 분할로 고정했다(seed {SEED}).

숫자 S/V/M은 필요 없다. 가상 조건의 참/거짓/미확인은 원문 고객사 사실로 바꾸지 않는다.
이번 카드의 대량 개인 원장·통제 재료는 가상 상황 서술이며 실제 개인 행/인증정보가 아니다.
분할 중복 0은 의미/문체/모델 사전학습 독립성의 증명이 아니다. 기존 풀의 계열 정보 부족은 검증 보고서에 남긴다.
봉인 후보는 파일 이름이며 실제 접근 통제·독립 검수 완료를 뜻하지 않는다.

다음: 기준 경계(특히 1,000명 제안) 검토 → 답안 독립 검토·불일치 조정 → 승인·재봉인 → 기존 동일 문서 라벨만 바꾼 A/B.
기존 교정20건과 이140건을 학습 데이터로 내보내지 않았다. 모델/운영 API/검수 경로는 변경하지 않았다.
"""
    with (out / "README.md").open("x", encoding="utf-8", newline="\n") as fh:
        fh.write(readme)
    manifest = {"schema_version": "content-reference-pack-v1", "created_at": datetime.now(timezone.utc).isoformat(),
                "policy_version": POLICY_ID, "policy_sha256": sha256(policy), "seed": SEED,
                "approval_status": "unapproved", "human_signature": None, "training_allowed": False,
                "model_loaded": False, "network_used": False, "splits": splits,
                "generator_sources": [{"path": f"scripts/{name}", "sha256": sha256(POC / "scripts" / name)}
                                      for name in ("prepare_content_reference.py", "content_reference_cases.py",
                                                   "content_reference_contract.py", "evaluation_inputs.py")],
                "files": [{"path": p.relative_to(out).as_posix(), "sha256": sha256(p)} for p in sorted(out.rglob("*")) if p.is_file()]}
    write_json(out / "manifest.json", manifest)
    return validation


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = build(POC / args.out)
    print(json.dumps({k: result[k] for k in ("status", "n", "grade_cases", "review_cases", "split")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

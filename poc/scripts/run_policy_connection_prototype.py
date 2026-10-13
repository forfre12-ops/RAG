"""20건 명시 메타데이터 정책 연결 프로토타입.

이 도구는 고객사 정확도를 측정하지 않는다. 기존 합성 문서 본문에 명시적인
출처·보안표시·접근범위를 붙였을 때 BERT 후보, 결정형 정책, LLM 보조가
어떤 문서를 ``needs_review`` 로 바꾸는지만 확인한다. 운영 API나 정책 파일을
변경하지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))

from koipa.modules.m3_labeling.policy_engine import Policy, Rule, evaluate, validate


GRADES = ("TS", "S1", "S2", "S3")
RANK = {grade: len(GRADES) - index for index, grade in enumerate(GRADES)}


def _policy() -> Policy:
    """가상 고객사 정책 v0.1.

    실제 고객사 정책이 아니다. 규칙마다 어느 시스템 메타데이터가 필요한지를
    보여 주기 위한 연결 fixture다.
    """
    return Policy(
        org_id="prototype-org",
        version="prototype-policy-v0.1",
        effective_date="2026-09-21",
        grade_order=GRADES,
        default_grade="S3",
        rules=(
            Rule("P-TS-MARK", "TS", 10,
                 {"security_marking": {"op": "eq", "value": "top_secret"}},
                 ("security_marking",), "명시 최고 보안표시"),
            # 이 규칙은 top_secret+공개가 동시에 들어온 비정상 packet을 의도적으로
            # 충돌시키며, 엔진이 더 높은 후보와 review를 같이 남기는지 확인한다.
            Rule("P-CONFLICT-PUBLIC", "S3", 10,
                 {"security_marking": {"op": "eq", "value": "top_secret"},
                  "public_disclosed": {"op": "eq", "value": True}},
                 ("security_marking", "public_disclosed"), "명시 보안표시와 공개출처 충돌"),
            Rule("P-S1-RESTRICTED", "S1", 20,
                 {"security_marking": {"op": "eq", "value": "secret"},
                  "access_scope": {"op": "eq", "value": "approved_only"}},
                 ("security_marking", "access_scope"), "비밀표시 및 승인자 제한"),
            Rule("P-S2-DEPARTMENT", "S2", 30,
                 {"access_scope": {"op": "eq", "value": "department"}},
                 ("access_scope",), "부서 제한"),
            Rule("P-S3-PUBLIC", "S3", 90,
                 {"public_disclosed": {"op": "eq", "value": True}},
                 ("public_disclosed",), "공개 출처"),
        ),
        note="명시 메타데이터 연결 시험용 가상 정책; 고객사 정책이 아님",
    )


def _load_source() -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {grade: [] for grade in GRADES}
    source = _POC / "datasets/gold/golden100_labeled_v3.jsonl"
    for line in source.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        grade = row.get("target")
        if grade in rows and row.get("text"):
            rows[grade].append(row)
    for grade in GRADES:
        rows[grade].sort(key=lambda row: str(row["doc_id"]))
        if len(rows[grade]) < 5:
            raise RuntimeError(f"{grade} source rows are insufficient")
    return rows


def _facts(source_type: str, marking: str | None, scope: str | None) -> dict:
    # source_type은 외부 계약 필드이고 policy_engine은 관측 사실 public_disclosed를 읽는다.
    # 여기서의 변환은 명시 source_type만 사용하며 본문에서 공개 여부를 추정하지 않는다.
    facts: dict[str, object] = {}
    if marking is not None:
        facts["security_marking"] = marking
    if scope is not None:
        facts["access_scope"] = scope
    if source_type == "public_real":
        facts["public_disclosed"] = True
    elif source_type in {"internal_created", "external_received"}:
        facts["public_disclosed"] = False
    return facts


def _cases() -> list[dict]:
    source = _load_source()
    cases: list[dict] = []

    def add(group: str, row: dict, source_type: str, marking: str | None,
            scope: str | None, expected_policy_review: bool) -> None:
        cases.append({
            "case_id": f"PC-{len(cases)+1:02d}",
            "group": group,
            "source_doc_id": row["doc_id"],
            # target은 문서 선별에만 쓰며 채점·프롬프트로 전달하지 않는다.
            "fixture_source_label_not_scored": row["target"],
            "text": row["text"],
            "source_type": source_type,
            "security_marking": marking,
            "access_scope": scope,
            "facts": _facts(source_type, marking, scope),
            "expected_policy_review": expected_policy_review,
        })

    for row in source["TS"][:4]:
        add("explicit-top-secret", row, "internal_created", "top_secret", "approved_only", False)
    for row in source["S1"][:4]:
        add("explicit-secret-restricted", row, "internal_created", "secret", "approved_only", False)
    # ``none``은 관측된 무표시다. None(아래 edge case)은 공급되지 않은 사실이며,
    # 두 상태를 같게 보내면 엔진의 안전한 보류 동작을 잘못된 검수로 오해하게 된다.
    for row in source["S2"][:4]:
        add("explicit-department", row, "internal_created", "none", "department", False)
    for row in source["S3"][:4]:
        add("explicit-public", row, "public_real", "none", "all_employees", False)

    # 네 건은 등급 정답이 아니라 evidence-missing/conflict 라우팅 확인용이다.
    add("missing-access-evidence", source["S1"][4], "internal_created", "secret", None, True)
    add("marking-public-conflict", source["S3"][4], "public_real", "top_secret", "approved_only", True)
    add("unmarked-internal", source["TS"][4], "internal_created", None, None, True)
    add("unmarked-external", source["S2"][4], "external_received", None, None, True)
    assert len(cases) == 20
    return cases


def _bert_predictions(cases: list[dict]) -> list[dict]:
    import torch
    import torch.nn.functional as functional
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    model_dir = _POC / "artifacts/classifier_p1_v5_clean/v-fe4b386b"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir), local_files_only=True).to(device)
    model.eval()
    id2label = {int(key): value for key, value in model.config.id2label.items()}
    results: list[dict] = []
    with torch.no_grad():
        for start in range(0, len(cases), 8):
            batch = cases[start:start + 8]
            encoding = tokenizer([case["text"] for case in batch], truncation=True,
                                 max_length=512, padding=True, return_tensors="pt").to(device)
            probs = functional.softmax(model(**encoding).logits, dim=-1).cpu()
            for case, probability in zip(batch, probs):
                index = int(probability.argmax().item())
                results.append({
                    "grade": id2label[index],
                    "confidence": float(probability[index]),
                    "device": device,
                })
    return results


def _llm_review(case: dict, bert: dict, proposal: dict, model: str) -> dict:
    """LLM은 등급을 바꾸지 않고 review 여부만 제안한다.

    운영 LLMLabeler는 등급 JSON 계약만 가지므로, 이 프로토타입은 독립된 좁은
    JSON 계약을 쓴다. LLM 사용량 DB에는 기록하지 않는다.
    """
    text = case["text"]
    excerpt = text if len(text) <= 3500 else text[:3000] + "\n[중략]\n" + text[-500:]
    user = {
        "bert_candidate": bert["grade"],
        "bert_confidence": round(bert["confidence"], 4),
        "policy": proposal,
        "typed_facts": {
            "source_type": case["source_type"],
            "security_marking": case["security_marking"],
            "access_scope": case["access_scope"],
        },
        "document": excerpt,
    }
    system = (
        "당신은 보안등급 검수 보조기다. 최종 등급을 확정하거나 낮추지 않는다. "
        "BERT 후보보다 더 민감한 등급이 필요할 가능성, 명시 메타데이터 충돌, "
        "또는 정책 증거 부족이 있으면 review_required=true로 답하라. "
        "아니면 false. JSON만 출력: "
        '{"review_required":true|false,"higher_grade":"TS|S1|S2|S3|null","reason":"짧은 근거"}'
    )
    body = json.dumps({"model": model, "stream": False, "format": "json", "think": False,
                       "options": {"temperature": 0, "seed": 20260921,
                                   "num_ctx": 8192, "num_predict": 180},
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]},
                      ensure_ascii=False).encode("utf-8")
    started = time.perf_counter()
    request = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = str((payload.get("message") or {}).get("content") or "")
        # Ollama ``format=json``도 모델이 앞뒤 설명·think 흔적을 붙이는 경우가 있다.
        # 운영 라벨러와 같은 방식으로 첫 JSON object만 추출한다. 추출 실패는 아래의
        # 안전한 review 폴백으로 남긴다.
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise ValueError("no_json_object")
        parsed = json.loads(match.group(0))
        if not isinstance(parsed, dict):
            raise ValueError("json_not_object")
        review_raw = parsed.get("review_required")
        review = review_raw is True or str(review_raw).strip().lower() in {"true", "yes", "1"}
        higher = parsed.get("higher_grade")
        higher = None if higher in (None, "", "null", "없음", "해당 없음") else str(higher).strip()
        # 이 prototype의 계약은 review 여부다. 등급 문자열이 비표준이면 숨기지 않고
        # schema_warning으로 남기되, LLM이 등급을 바꾸는 데는 절대 사용하지 않는다.
        schema_warning = None if higher in (*GRADES, None) else "nonstandard_higher_grade"
        return {"ok": True, "review": review, "higher_grade": higher if schema_warning is None else None,
                "schema_warning": schema_warning,
                "reason": str(parsed.get("reason") or "")[:300],
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}
    except Exception as exc:  # LLM 오류는 안전하게 검수로 보낸다.
        return {"ok": False, "review": True, "higher_grade": None,
                "reason": f"llm_error:{type(exc).__name__}",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}


def main() -> int:
    parser = argparse.ArgumentParser(description="typed metadata policy connection prototype")
    parser.add_argument("--out", default="reports/policy_connection_prototype_20260921")
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--llm-model", default="qwen3:14b")
    args = parser.parse_args()

    policy = _policy()
    issues = validate(policy)
    if issues:
        raise SystemExit("invalid fixture policy: " + "; ".join(issues))
    cases = _cases()
    bert = _bert_predictions(cases)
    rows: list[dict] = []
    for case, bert_result in zip(cases, bert):
        proposal_obj = evaluate(policy, case["facts"])
        proposal = proposal_obj.to_dict()
        # 정책 후보가 BERT와 다르거나 증거 부족/충돌이면 등급을 바꾸지 않고 검수로 보낸다.
        mismatch = proposal["grade"] in RANK and proposal["grade"] != bert_result["grade"]
        policy_review = bool(proposal["needs_review"] or mismatch)
        expectation_ok = policy_review == case["expected_policy_review"] or (
            not case["expected_policy_review"] and mismatch
        )
        llm = None if args.skip_llm else _llm_review(case, bert_result, proposal, args.llm_model)
        final_review = policy_review or bool(llm and llm["review"])
        rows.append({
            "case_id": case["case_id"], "group": case["group"],
            "source_doc_id": case["source_doc_id"],
            "text_sha256": hashlib.sha256(case["text"].encode("utf-8")).hexdigest(),
            "text_chars": len(case["text"]),
            "typed_metadata": {key: case[key] for key in ("source_type", "security_marking", "access_scope")},
            "facts": case["facts"], "bert": bert_result, "policy": proposal,
            "policy_mismatch": mismatch, "policy_review": policy_review,
            "expected_policy_review": case["expected_policy_review"],
            "policy_expectation_ok": expectation_ok, "llm": llm,
            "final_review": final_review,
        })

    summary = {
        "purpose": "typed-metadata policy wiring only; not accuracy or customer validation",
        "fixture_origin": "synthetic golden100_v3 texts; source labels are not scored or sent to LLM",
        "policy": {"org_id": policy.org_id, "version": policy.version,
                   "effective_date": policy.effective_date, "rules": [rule.id for rule in policy.rules]},
        "n": len(rows), "llm_model": None if args.skip_llm else args.llm_model,
        "bert_device": rows[0]["bert"]["device"],
        "policy_expectation_failures": [row["case_id"] for row in rows if not row["policy_expectation_ok"]],
        "bert_distribution": dict(Counter(row["bert"]["grade"] for row in rows)),
        "policy_review_n": sum(row["policy_review"] for row in rows),
        "llm_incremental_review_n": sum(bool(row["llm"] and row["llm"]["review"] and not row["policy_review"]) for row in rows),
        "final_review_n": sum(row["final_review"] for row in rows),
        "llm_failures": [row["case_id"] for row in rows if row["llm"] and not row["llm"]["ok"]],
        "invariant": "policy and LLM never replace BERT grade; they only set review",
    }
    out = _POC / args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "records.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

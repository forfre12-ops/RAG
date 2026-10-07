#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""최종 모의문서(사실 근거 문서) 패키지 생성 — 1~5차의 검증 통과 문서를 합쳐 봉인/학습/개발·표현시험으로 나누고 전문가 검수용 파일을 만든다.

입력: reports/CLAUDE_DOCGEN_{20260921,R2_20260921,R3_20260921,R4_20260921,R5_20260921}/ 의
      pilot_docs_checked.jsonl · pilot_judge_rows.json(블라인드 판정=명세 일치 여부) · specs_pilot.json
분할(가족 단위, 결과를 보고 바꾸지 않는다 — 손잡이는 아래 상수):
  · 봉인(sealed)   = 5차 가족 중 4건 모두 검증 통과한 가족에서 SEALED_FAMILIES 개(무작위, 시드 고정). 학습·튜닝·분석에 쓰지 않는다.
  · 개발·표현 시험 = 3차 전체 + 4차 시험 가족(split=test4)
  · 학습           = 나머지(1·2차 전체 + 4차 학습 가족 + 5차 나머지)
라벨 = 가이드 12쪽 곱셈표(S×V×M) 계산값. 블라인드 판정 재현으로 검증된 문서만 포함. 전문가 검수 전 잠정 라벨이다.
출력: datasets/mock_final_factfirst_20260921/
  reviewer_documents_sealed.jsonl · reviewer_documents_all.jsonl  (review_id + text 만 — 등급·근거·가족 정보 없음)
  internal_manifest.jsonl (review_id → 라벨·S/V/M·label_basis·typed_facts·차수·가족·분할 …)  · splits.json · SUMMARY.json · README.md
사용:  python scripts/build_final_mock_set.py
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
ROUNDS = {"R1": "CLAUDE_DOCGEN_20260921", "R2": "CLAUDE_DOCGEN_R2_20260921", "R3": "CLAUDE_DOCGEN_R3_20260921", "R4": "CLAUDE_DOCGEN_R4_20260921", "R5": "CLAUDE_DOCGEN_R5_20260921"}
OUT = POC / "datasets" / "mock_final_factfirst_20260921"
SEALED_FAMILIES = 50
SEED = 20261002
G = ["TS", "S1", "S2", "S3"]
SRC = {0: "public", 1: "external_confidential", 2: "internal"}
ACC = {0: "all_employees", 1: "department", 2: "approved_only"}     # ICD §3.3


def load_round(tag: str, folder: str) -> list[dict]:
    d = POC / "reports" / folder
    docs = [json.loads(x) for x in (d / "pilot_docs_checked.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    ok = {r["doc_key"] for r in json.loads((d / "pilot_judge_rows.json").read_text(encoding="utf-8")) if r["ok"]}
    specs = {s["doc_key"]: s for s in json.loads((d / "specs_pilot.json").read_text(encoding="utf-8"))}
    out = []
    for x in docs:
        if x["doc_key"] not in ok:
            continue
        s = specs[x["doc_key"]]
        vn = s.get("v_numbers") or {}
        out.append({"round": tag, "doc_key": x["doc_key"], "family_id": x["family_id"], "text": x["text"], "grade": x["grade"], "S": s["S"], "V": s["V"], "M": s["M"],
                    "domain": x.get("domain"), "form": x.get("form"), "delta_axis": s.get("delta_axis"), "r4_split": s.get("split"),
                    "label_basis": s.get("label_basis") or f"S×V×M = {s['S']}×{s['V']}×{s['M']} = {s['S'] * s['V'] * s['M']} → {x['grade']} (가이드 12쪽 곱셈표)",
                    "typed_facts": s.get("typed_facts") or {"source_type": SRC[s["S"]], "access_scope": ACC[s["M"]],
                                                            "value_evidence": {"attributed_cost_man_won": vn.get("cost_man_won", 0), "attributed_hours": vn.get("hours", 0), "economic_use": s["V"] > 0}}})
    return out


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    allv, missing = [], []
    for tag, folder in ROUNDS.items():
        try:
            allv += load_round(tag, folder)
        except FileNotFoundError as e:
            missing.append(f"{tag}: {e.filename}")
    print("검증 통과 문서", Counter(x["round"] for x in allv), "· 누락", missing)
    rng = random.Random(SEED)
    r5_full = sorted(f for f in {x["family_id"] for x in allv if x["round"] == "R5"} if sum(1 for x in allv if x["family_id"] == f) == 4)
    rng.shuffle(r5_full)
    sealed_f = set(r5_full[:SEALED_FAMILIES])
    for x in allv:
        if x["family_id"] in sealed_f:
            x["split"] = "sealed"
        elif x["round"] == "R3" or (x["round"] == "R4" and x["r4_split"] == "test4"):
            x["split"] = "dev_expression"
        else:
            x["split"] = "train"
    order = list(range(len(allv)))
    rng.shuffle(order)
    for n, i in enumerate(order):
        allv[i]["review_id"] = f"FD-{n + 1:04d}"
    OUT.mkdir(parents=True, exist_ok=True)

    def w(name: str, rows: list[dict]) -> None:
        with (OUT / name).open("w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    by_id = sorted(allv, key=lambda x: x["review_id"])
    w("reviewer_documents_all.jsonl", [{"review_id": x["review_id"], "text": x["text"]} for x in by_id])
    w("reviewer_documents_sealed.jsonl", [{"review_id": x["review_id"], "text": x["text"]} for x in by_id if x["split"] == "sealed"])
    w("internal_manifest.jsonl", [{k: x[k] for k in ("review_id", "round", "doc_key", "family_id", "split", "grade", "S", "V", "M", "label_basis", "typed_facts", "domain", "form", "delta_axis")} for x in by_id])
    splits = {s: [x["review_id"] for x in by_id if x["split"] == s] for s in ("sealed", "dev_expression", "train")}
    (OUT / "splits.json").write_text(json.dumps(splits, ensure_ascii=False), encoding="utf-8")
    summ = {"total": len(allv), "by_split": {s: len(v) for s, v in splits.items()},
            "by_split_grade": {s: dict(Counter(x["grade"] for x in allv if x["split"] == s)) for s in splits},
            "by_round": dict(Counter(x["round"] for x in allv)), "sealed_families": sorted(sealed_f)[:3] and len(sealed_f),
            "family_disjoint": len({x["family_id"] for x in allv if x["split"] == "sealed"} & {x["family_id"] for x in allv if x["split"] != "sealed"}) == 0}
    (OUT / "SUMMARY.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "README.md").write_text(f"""# 최종 모의문서(사실 근거 문서) — 전문가 검수·학습·평가 패키지

- 총 {len(allv)}건 = 1~5차 사실 우선 생성 문서 중 **블라인드 판정 재현으로 검증된 문서**. 등급은 가이드 12쪽 곱셈표(S×V×M)로 계산한 **잠정 라벨**(전문가 검수 전, 사람 확정 아님).
- 분할(가족 단위, 가족이 두 분할에 걸치지 않음): 봉인 {len(splits['sealed'])} · 개발·표현시험 {len(splits['dev_expression'])} · 학습 {len(splits['train'])}.
- **전문가에게는 `reviewer_documents_sealed.jsonl`(또는 검수 대상 부분집합)만 준다** — review_id 와 본문뿐이다. `internal_manifest.jsonl` 에는 잠정 라벨·S/V/M 이 있어 검수자에게 주면 안 된다.
- 봉인 문서는 학습·튜닝·미탐 분석에 쓰지 않는다. 검수 라벨이 확정된 뒤 최종 평가 때 한 번만 연다.
- typed_facts(source_type·access_scope 는 ICD §3 계약값, value_evidence 는 새 필드)는 정책 엔진 시험용이며 검수자에게 주지 않는다.
- 산식 미확정: S1 문서 전부가 가이드 곱셈표와 운영 v22 가 갈리는 조합((1,2,2)(2,1,2)(2,2,1))이고 (2,2,0) S3 도 해당한다(발주처 결정 D01~D08). 문서마다 S/V/M 을 저장하므로 다른 산식이 정해지면 표 조회로 라벨을 다시 계산한다.
- 생성·검증 기록: reports/CLAUDE_DOCGEN_*/PREREG*.md · 검사 도구 scripts/validate_docgen_pilot.py · 이 패키지 생성 scripts/build_final_mock_set.py
""", encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False))
    print({f.name: sha(f)[:12] for f in OUT.glob("*.jsonl")})
    return 0


if __name__ == "__main__":
    sys.exit(main())

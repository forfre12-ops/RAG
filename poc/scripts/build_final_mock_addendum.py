#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""최종 세트(1,000건) 뒤에 만든 6·7차 문서를 부속 패키지로 묶는다 — 학습 증량(7차)과 독립 작성 모델 시험(6차).
입력: reports/CLAUDE_DOCGEN_{R6,R7}_20260921 의 pilot_docs_checked.jsonl · pilot_judge_rows.json(판정=명세) · specs_pilot.json · WRITER_MODELS_PRIVATE.json
분할: 7차 검증 통과 문서 = 'train_r7'(가족 단위, 학습에만) · 6차 검증 통과 문서(haiku 포함) = 'test_r6_independent' · 8차 검증 통과 문서 = 'test_r8_style_shift'(둘 다 학습에 안 씀, 작성 모델별 표시)
출력: datasets/mock_final_factfirst_20260921/addendum_r6_r7/  documents_all.jsonl(review_id+본문) · internal_manifest.jsonl · SUMMARY.json · README.md
      review_id 는 'FA-####'(최종 세트의 FD-#### 와 겹치지 않음). 전문가에게 주는 용도가 아니다 — 봉인 200건은 기존 패키지의 reviewer_documents_sealed.jsonl 뿐.
사용:  python scripts/build_final_mock_addendum.py
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
ROUNDS = {"R6": "CLAUDE_DOCGEN_R6_20260921", "R7": "CLAUDE_DOCGEN_R7_20260921", "R8": "CLAUDE_DOCGEN_R8_20260921"}
OUT = POC / "datasets" / "mock_final_factfirst_20260921" / "addendum_r6_r7"
SEED = 20261103


def load(tag: str, folder: str) -> list[dict]:
    d = POC / "reports" / folder
    docs = [json.loads(x) for x in (d / "pilot_docs_checked.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    ok = {r["doc_key"] for r in json.loads((d / "pilot_judge_rows.json").read_text(encoding="utf-8")) if r["ok"]}
    specs = {s["doc_key"]: s for s in json.loads((d / "specs_pilot.json").read_text(encoding="utf-8"))}
    fam_model = json.loads((d / "WRITER_MODELS_PRIVATE.json").read_text(encoding="utf-8"))["family_model"]
    out = []
    for x in docs:
        if x["doc_key"] not in ok:
            continue
        s = specs[x["doc_key"]]
        out.append({"round": tag, "doc_key": x["doc_key"], "family_id": x["family_id"], "text": x["text"], "grade": x["grade"], "S": s["S"], "V": s["V"], "M": s["M"],
                    "writer_model": fam_model[x["family_id"]], "family_kind": s.get("kind") or s.get("family_type"), "delta_axis": s.get("delta_axis"), "label_basis": s["label_basis"],
                    "typed_facts": s["typed_facts"], "split": {"R7": "train_r7", "R6": "test_r6_independent", "R8": "test_r8_style_shift"}[tag]})
    return out


def main() -> int:
    rows = []
    for tag, folder in ROUNDS.items():
        rows += load(tag, folder)
    order = list(range(len(rows)))
    random.Random(SEED).shuffle(order)
    for n, i in enumerate(order):
        rows[i]["review_id"] = f"FA-{n + 1:04d}"
    rows.sort(key=lambda r: r["review_id"])
    OUT.mkdir(parents=True, exist_ok=True)

    def w(name: str, rs: list[dict]) -> None:
        with (OUT / name).open("w", encoding="utf-8", newline="\n") as fh:
            for r in rs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    w("documents_all.jsonl", [{"review_id": r["review_id"], "text": r["text"]} for r in rows])
    w("internal_manifest.jsonl", [{k: r[k] for k in ("review_id", "round", "doc_key", "family_id", "split", "writer_model", "family_kind", "grade", "S", "V", "M", "delta_axis", "label_basis", "typed_facts")} for r in rows])
    summ = {"total": len(rows), "by_split": dict(Counter(r["split"] for r in rows)), "by_round_writer": {f"{r_}|{w_}": n for (r_, w_), n in Counter((r["round"], r["writer_model"]) for r in rows).items()},
            "by_split_grade": {s: dict(Counter(r["grade"] for r in rows if r["split"] == s)) for s in ("train_r7", "test_r6_independent", "test_r8_style_shift")}, "family_disjoint_r6_r7": not ({r["family_id"] for r in rows if r["round"] == "R6"} & {r["family_id"] for r in rows if r["round"] == "R7"})}
    (OUT / "SUMMARY.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "README.md").write_text(f"""# 최종 모의문서 부속 패키지 — 6차(독립 작성 모델 시험) · 7차(학습 증량) · 8차(문체·양식 변형 시험)

- 총 {len(rows)}건 = 판정=명세로 검증된 6차 {sum(1 for r in rows if r['round'] == 'R6')}건 + 7차 {sum(1 for r in rows if r['round'] == 'R7')}건 + 8차 {sum(1 for r in rows if r['round'] == 'R8')}건. 등급은 곱셈표(S×V×M) 계산값을 **다른 계열 판정자(opus·fable)** 가 재현한 잠정 라벨이다(전문가 검수 전).
- `train_r7` = 7차(작성 opus·sonnet, 경계 대조 가족 Q·희귀 조합 자유 가족 P): 학습 전용. `test_r6_independent` = 6차(작성 haiku·opus·fable): 학습에 쓰지 않는 독립 작성 모델 시험(haiku 문서는 판정을 통과한 소수뿐이라 해석 주의). `test_r8_style_shift` = 8차(작성 opus·fable, 새 업무 유형·비정형 문체): 학습에 쓰지 않는 문체 변형 시험.
- 이 파일들은 **전문가 검수용이 아니다.** 봉인 200건은 `../reviewer_documents_sealed.jsonl` 뿐이며 이 부속 패키지와 가족·문서가 겹치지 않는다.
- 생성: scripts/build_final_mock_addendum.py · 생성·검증 기록 reports/CLAUDE_DOCGEN_R6_20260921·R7_20260921 (PREREG_R6.md·PREREG_R7.md·PREREG_R8.md)
""", encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False))
    print({f.name: hashlib.sha256(f.read_bytes()).hexdigest()[:12] for f in OUT.glob("*.jsonl")})
    return 0


if __name__ == "__main__":
    sys.exit(main())

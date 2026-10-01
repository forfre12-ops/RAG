#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""전문가 검수 전체 패키지 — 학습에 쓴 문서(963+67)와 봉인 200 + 우리 내부 시험 354, 전부 검수자에게 보낸다.

배경(2026-09-22): "학습데이터 포함해서, 봉인 200건 포함해서, 검수자에게 검수를 받고 싶다."
네 묶음으로 review_batch 를 나눈다(코드가 이미 review_batch 필터를 지원 — api/golden.py proxy_gold_candidate_list):
  fs7_train_loss      963건  FS7 학습 손실 계산에 직접 쓴 문서(최종 세트 학습분 579 + 7차 384). 검수 결과 = 라벨 품질 검증용.
                              **재현율·미탐율 계산에 다시 쓰면 안 된다**(모델이 이 문서로 학습함 — 암기와 실력을 구분 못 함).
  fs7_val_holdout       67건  학습 손실엔 안 들어갔지만 체크포인트(에폭) 선택에 영향. 위와 같이 조심해서 다룬다.
  eval_dev_never_trained 354건  개발·표현 154 + 6차 86 + 8차 114. 모델이 전혀 안 봄 — 검수 결과를 그대로 골든셋으로 승격 가능.
  golden_sealed_untouched 200건  봉인. 우리도 안 열어봄. 가장 깨끗한 골든셋 후보.
7차(R7)는 내부적으로 doc_key(예 'Q01-TS')를 쓰는데 **등급이 그대로 박혀 있어** 검수자에게 노출하면 안 된다 —
addendum internal_manifest.jsonl 로 review_id(FA-####)로 되돌려 맞춘다(사전 검증: 384/384 매핑 확인, 9/22).

출력: datasets/expert_review_package_20260922/
  documents_all.jsonl        검수자에게 줄 파일. 열 = {review_id, text} 뿐(등급·S/V/M·review_batch 없음)
  documents_by_batch/<batch>.jsonl   같은 내용을 묶음별로 나눠 둔 사본(콘솔 review_batch 적재용)
  internal_manifest.jsonl    review_id → review_batch·grade·S/V/M·round 등(검수자에게 절대 안 줌)
  SUMMARY.json
사용:  python scripts/build_expert_review_package.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
FINAL = POC / "datasets" / "mock_final_factfirst_20260921"
ADD = FINAL / "addendum_r6_r7"
TRAIN = POC / "reports" / "mock_final_train_20260921"
OUT = POC / "datasets" / "expert_review_package_20260922"


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    final_man = {m["review_id"]: m for m in load_jsonl(FINAL / "internal_manifest.jsonl")}
    final_docs = {r["review_id"]: r["text"] for r in load_jsonl(FINAL / "reviewer_documents_all.jsonl")}
    add_man = {m["review_id"]: m for m in load_jsonl(ADD / "internal_manifest.jsonl")}
    add_docs = {r["review_id"]: r["text"] for r in load_jsonl(ADD / "documents_all.jsonl")}
    key2rid_r7 = {m["doc_key"]: m["review_id"] for m in add_man.values() if m["round"] == "R7"}

    fs_train_ids = {x["doc_id"] for x in load_jsonl(TRAIN / "fs_train.jsonl")}          # 579, review_id 그대로
    fs_val_ids = {x["doc_id"] for x in load_jsonl(TRAIN / "fs_val.jsonl")}              # 67, review_id 그대로
    fs7_r7_keys = {x["doc_id"] for x in load_jsonl(TRAIN / "fs7_train.jsonl")} - fs_train_ids  # 384, doc_key(등급 노출) 그대로
    fs7_r7_ids = {key2rid_r7[k] for k in fs7_r7_keys if k in key2rid_r7}
    assert len(fs7_r7_ids) == len(fs7_r7_keys) == 384, (len(fs7_r7_ids), len(fs7_r7_keys))

    rows = []
    for rid, m in final_man.items():
        if rid in fs_train_ids:
            batch = "fs7_train_loss"
        elif rid in fs_val_ids:
            batch = "fs7_val_holdout"
        elif m["split"] == "sealed":
            batch = "golden_sealed_untouched"
        else:  # dev_expression
            batch = "eval_dev_never_trained"
        rows.append({"review_id": rid, "text": final_docs[rid], "review_batch": batch, "round": m["round"], "split": m["split"],
                     "grade": m["grade"], "S": m["S"], "V": m["V"], "M": m["M"], "label_basis": m["label_basis"]})
    for rid, m in add_man.items():
        batch = "fs7_train_loss" if rid in fs7_r7_ids else "eval_dev_never_trained"  # R6·R8 = 시험용(학습 안 씀), R7 검증 통과분 = 학습분
        rows.append({"review_id": rid, "text": add_docs[rid], "review_batch": batch, "round": m["round"], "split": m["split"],
                     "grade": m["grade"], "S": m["S"], "V": m["V"], "M": m["M"], "label_basis": m["label_basis"]})

    assert len({r["review_id"] for r in rows}) == len(rows), "review_id 중복"
    by_batch = Counter(r["review_batch"] for r in rows)
    assert by_batch == {"fs7_train_loss": 963, "fs7_val_holdout": 67, "eval_dev_never_trained": 354, "golden_sealed_untouched": 200}, dict(by_batch)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "documents_by_batch").mkdir(exist_ok=True)

    def w(path: Path, rs: list[dict]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for r in rs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    rows.sort(key=lambda r: r["review_id"])
    w(OUT / "documents_all.jsonl", [{"review_id": r["review_id"], "text": r["text"]} for r in rows])
    for b in sorted(by_batch):
        w(OUT / "documents_by_batch" / f"{b}.jsonl", [{"review_id": r["review_id"], "text": r["text"]} for r in rows if r["review_batch"] == b])
    w(OUT / "internal_manifest.jsonl", rows)
    summ = {"total": len(rows), "by_batch": dict(by_batch), "by_batch_grade": {b: dict(Counter(r["grade"] for r in rows if r["review_batch"] == b)) for b in by_batch},
            "note": {"fs7_train_loss": "모델 학습 손실에 직접 쓴 문서 — 검수 결과는 라벨 품질 검증용, 모델 성능(재현율·미탐율) 계산에 재사용 금지",
                     "fs7_val_holdout": "체크포인트 선택에만 영향 — 위와 같이 조심해서 다룰 것",
                     "eval_dev_never_trained": "모델 학습에 전혀 안 씀 — 검수 결과를 그대로 골든셋(평가 정답)으로 승격 가능",
                     "golden_sealed_untouched": "우리도 아직 안 연 문서 — 가장 깨끗한 골든셋 후보"}}
    (OUT / "SUMMARY.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "README.md").write_text(f"""# 전문가 검수 전체 패키지 (2026-09-22)

- 총 {len(rows)}건. `documents_all.jsonl`(review_id+본문만, 등급·S/V/M 없음)이 검수자에게 줄 파일이다. 등급별 배분은 `SUMMARY.json` 참고.
- **`documents_by_batch/`** 는 같은 문서를 4묶음으로 나눈 사본이다(콘솔 적재 시 `review_batch` 태그로 그대로 쓸 수 있게):
  - `fs7_train_loss.jsonl` (963) — 모델이 학습에 직접 쓴 문서. 검수 결과는 **라벨 품질 검증용**이며 모델 재현율·미탐율 재계산에 쓰면 안 된다(train-on-test).
  - `fs7_val_holdout.jsonl` (67) — 체크포인트 선택에만 영향. 위와 동일하게 조심해서 다룬다.
  - `eval_dev_never_trained.jsonl` (354) — 모델이 전혀 안 본 우리 내부 시험 문서(개발·표현 154+6차 86+8차 114). 검수 결과를 그대로 **골든셋(평가 정답)**으로 승격할 수 있다.
  - `golden_sealed_untouched.jsonl` (200) — 우리도 아직 안 열어본 봉인 문서. 가장 깨끗한 골든셋 후보.
- `internal_manifest.jsonl` 에는 review_batch·라운드·등급·S/V/M 이 들어 있다 — **검수자에게 절대 주지 않는다.**
- 라벨은 전부 AI 가 곱셈표로 계산하고 AI 판정자가 재현한 잠정 라벨이다. 전문가 확정 전까지는 정답이 아니다.
- 다음 단계(미실행): 콘솔(`ProxyGoldCandidateService`)이 읽는 `{{doc_id}}.md`+`{{doc_id}}.metadata.json` 형식으로 변환해 적재 → 전문가 계정 발급 → 콘솔에서 결정+서명 → 서명분은 `golden_tiers.tier_of()` 가 자동으로 `locked_gold_eval` 로 계산해 `datasets/gold_real/locked_eval.jsonl` 에 쌓인다(등급당 5건 이상이면 `eval_readiness()` 통과).
""", encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False))
    print({f.name: hashlib.sha256(f.read_bytes()).hexdigest()[:12] for f in OUT.glob("*.jsonl")})
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검수자 전달용 — 1~9차 검증 통과 문서 전부(1,731건)를 폴더 하나에 배치.

집계 대상: 각 라운드 reports/CLAUDE_DOCGEN_{...}/ 의 pilot_docs_checked.jsonl(본문) ·
pilot_judge_rows.json(블라인드 판정=명세, ok 만) · specs_pilot.json(S/V/M·등급·label_basis·typed_facts) ·
WRITER_MODELS_PRIVATE.json(작성 모델·가족). 문서 수는 scripts/build_expert_review_all_1731.py 실행 시
직접 세어 찍는다(9/24 재확인: R1 233·R2 228·R3 115·R4 151·R5 273·R6 86·R7 384·R8 114·R9 147 = 1,731).

리메모: review_id 는 이 폴더 전용으로 새로 발급한다(가족 단위 순서를 섞어 등급·차수 군집이
안 드러나게 한다). doc_key(예 'N01-TS')는 등급을 담고 있어 검수자에게 절대 노출하지 않는다.

출력: datasets/expert_review_all_1731_20260924/
  documents_all.jsonl        검수자에게 줄 파일. 열 = {review_id, text} 뿐.
  internal_manifest.jsonl    review_id → round·family_id·grade·S/V/M·label_basis·typed_facts·writer_model·training_use
                              (검수자에게 절대 안 준다 — 라벨·학습 이력이 다 들어 있다)
  SUMMARY.json · README.md
사용:  python scripts/build_expert_review_all_1731.py
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
ROUNDS = {"R1": "CLAUDE_DOCGEN_20260921", "R2": "CLAUDE_DOCGEN_R2_20260921", "R3": "CLAUDE_DOCGEN_R3_20260921",
          "R4": "CLAUDE_DOCGEN_R4_20260921", "R5": "CLAUDE_DOCGEN_R5_20260921", "R6": "CLAUDE_DOCGEN_R6_20260921",
          "R7": "CLAUDE_DOCGEN_R7_20260921", "R8": "CLAUDE_DOCGEN_R8_20260921", "R9": "CLAUDE_DOCGEN_R9_20260921"}
OUT = POC / "datasets" / "expert_review_all_1731_20260924"
SEED = 20260924
TRAIN_FS7_R7 = {"R7"}          # FS7 학습 손실에 통째로 들어간 라운드
TRAIN_FS9_R9 = {"R9"}          # FS9 에서 추가로 학습 손실에 들어간 라운드(9/22)
NEVER_TRAINED = {"R3", "R6", "R8"}   # 시험 전용, 학습에 전혀 안 씀
# R1·R2·R4·R5 는 최종 세트 안에서 학습(646)/개발표현시험(154)/봉인(200)으로 다시 갈리므로
# 아래에서 datasets/mock_final_factfirst_20260921/internal_manifest.jsonl 의 split 값을 그대로 쓴다.


def load_round(tag: str, folder: str) -> list[dict]:
    d = POC / "reports" / folder
    docs = [json.loads(x) for x in (d / "pilot_docs_checked.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    ok = {r["doc_key"] for r in json.loads((d / "pilot_judge_rows.json").read_text(encoding="utf-8")) if r["ok"]}
    specs = {s["doc_key"]: s for s in json.loads((d / "specs_pilot.json").read_text(encoding="utf-8"))}
    fam_model = json.loads((d / "WRITER_MODELS_PRIVATE.json").read_text(encoding="utf-8"))["family_model"] if (d / "WRITER_MODELS_PRIVATE.json").exists() else {}
    out = []
    for x in docs:
        if x["doc_key"] not in ok:
            continue
        s = specs[x["doc_key"]]
        vn = s.get("v_numbers") or {}
        if tag in TRAIN_FS7_R7 or tag in TRAIN_FS9_R9:
            use = "fs_train_loss"
        elif tag in NEVER_TRAINED:
            use = "never_trained"
        else:
            use = None  # R1/R2/R4/R5 — 최종 세트 split 을 따로 붙인다
        out.append({"round": tag, "doc_key": x["doc_key"], "family_id": x["family_id"], "text": x["text"], "grade": x["grade"],
                    "S": s["S"], "V": s["V"], "M": s["M"], "writer_model": fam_model.get(x["family_id"], "sonnet_or_mixed"),
                    "label_basis": s.get("label_basis") or f"S×V×M = {s['S']}×{s['V']}×{s['M']} = {s['S'] * s['V'] * s['M']} → {x['grade']} (가이드 12쪽 곱셈표)",
                    "typed_facts": s.get("typed_facts") or {}, "training_use": use})
    return out


def main() -> int:
    rows = []
    for tag, folder in ROUNDS.items():
        rows += load_round(tag, folder)
    # 최종 세트(R1/R2/R4/R5)의 학습·검증·개발표현·봉인 구분을 그대로 가져온다(중복 산출 방지, 기존 스크립트가 이미 정한 것).
    final_man = {m["doc_key"]: m for m in (json.loads(x) for x in (POC / "datasets" / "mock_final_factfirst_20260921" / "internal_manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip())}
    fs_train_ids, fs_val_ids = None, None
    tr_dir = POC / "reports" / "mock_final_train_20260921"
    if (tr_dir / "fs_train.jsonl").exists():
        fs_train_ids = {json.loads(x)["doc_id"] for x in (tr_dir / "fs_train.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()}
        fs_val_ids = {json.loads(x)["doc_id"] for x in (tr_dir / "fs_val.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()}
    for r in rows:
        if r["training_use"] is not None:
            continue
        m = final_man.get(r["doc_key"])
        if m is None:
            r["training_use"] = "unknown"
            continue
        rid = m["review_id"]
        if fs_train_ids and rid in fs_train_ids:
            r["training_use"] = "fs_train_loss"
        elif fs_val_ids and rid in fs_val_ids:
            r["training_use"] = "fs_val_holdout"
        elif m["split"] == "sealed":
            r["training_use"] = "never_trained_sealed"
        else:
            r["training_use"] = "never_trained"

    assert len({(r["round"], r["doc_key"]) for r in rows}) == len(rows), "중복 문서"
    order = list(range(len(rows)))
    random.Random(SEED).shuffle(order)             # 등급·차수 군집이 순서에 안 드러나게 섞는다
    for n, i in enumerate(order):
        rows[i]["review_id"] = f"MD-{n + 1:04d}"
    rows.sort(key=lambda r: r["review_id"])

    OUT.mkdir(parents=True, exist_ok=True)

    def w(name: str, rs: list[dict]) -> None:
        with (OUT / name).open("w", encoding="utf-8", newline="\n") as fh:
            for r in rs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    w("documents_all.jsonl", [{"review_id": r["review_id"], "text": r["text"]} for r in rows])
    w("internal_manifest.jsonl", [{k: r[k] for k in ("review_id", "round", "doc_key", "family_id", "grade", "S", "V", "M", "label_basis", "typed_facts", "writer_model", "training_use")} for r in rows])
    summ = {"total": len(rows), "by_round": dict(Counter(r["round"] for r in rows)), "by_grade": dict(Counter(r["grade"] for r in rows)),
            "by_training_use": dict(Counter(r["training_use"] for r in rows)),
            "by_training_use_grade": {u: dict(Counter(r["grade"] for r in rows if r["training_use"] == u)) for u in {r["training_use"] for r in rows}}}
    (OUT / "SUMMARY.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "README.md").write_text(f"""# 전문가 검수 전달 패키지 — 사실 우선 모의문서 전량 {len(rows)}건 (2026-09-24)

- `documents_all.jsonl` 이 검수자에게 줄 파일이다. 열 = `review_id`(MD-####) · `text` 뿐이다. 등급·S/V/M·라운드·학습 이력 어디에도 없다.
- 라벨은 전부 가이드 12쪽 곱셈표(S×V×M) 계산값을 AI 판정자가 재현한 **잠정 라벨**이다. 전문가가 확정하기 전 정답이 아니다.
- `internal_manifest.jsonl` 은 review_id → 라운드·가족·등급·S/V/M·작성 모델·`training_use` 를 담는다. **검수자에게 절대 주지 않는다.**
- **`training_use` 읽는 법**(결과를 나중에 어떻게 쓸 수 있는지가 여기 갈린다):
  - `fs_train_loss` — 모델이 학습 손실에 직접 쓴 문서. 검수 결과는 **라벨 품질 검증용**이며 모델 재현율·미탐율 재계산에 쓰면 train-on-test가 된다.
  - `fs_val_holdout` — 체크포인트(에폭) 선택에만 영향. 위와 같이 조심해서 다룬다.
  - `never_trained` / `never_trained_sealed` — 모델이 전혀 안 본 문서. 검수 결과를 그대로 **골든셋(평가 정답)**으로 승격할 수 있다. `sealed` 는 우리도 아직 안 열어본 문서다.
  - `unknown` — 최종 세트 매핑에서 못 찾은 경우(0건이어야 한다, SUMMARY.json 로 확인).
- 구성: {json.dumps(summ['by_round'], ensure_ascii=False)} · 등급 {json.dumps(summ['by_grade'], ensure_ascii=False)} · 학습 이력 {json.dumps(summ['by_training_use'], ensure_ascii=False)}
- 생성: scripts/build_expert_review_all_1731.py · 각 라운드 생성·검증 기록은 reports/CLAUDE_DOCGEN_*/PREREG_*.md
""", encoding="utf-8")
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    print({f.name: hashlib.sha256(f.read_bytes()).hexdigest()[:12] for f in OUT.glob("*.jsonl")})
    return 0


if __name__ == "__main__":
    sys.exit(main())

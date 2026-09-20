#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""v5_clean 을 정책 라벨로 정정한 학습셋을 만든다 — 공개 판결문은 S3.

정정 규칙(둘 다 기계적이며 문서를 골라 고치지 않는다):
  ① LLM 이 규칙 S3 를 덮어쓴 65행 — label_source=llm_judge_primary ∧ rule_grade=S3 ∧ label≠S3 (build_v5_clean_s3fix.py 와 같은 규칙)
  ② 현재 판결문 검출기(build_p1_v5_clean.is_public_ruling, 소송 표제 인식 포함)에 적중하는데 라벨이 S3 가 아닌 행 → S3
     (v5_clean 은 옛 검출기의 놓침 62건 중 52건이 TS 33·S2 13·S1 6 으로 남아 있었다 — 메모리 train-set-llm-overrode-rule-s3-56-candidates)

근거: 공개 판결문은 비공지성 실패라 내용의 민감도와 무관하게 S3 다(프로젝트 정책, apply_public_ruling_rule).
검증: 5분할 교차검증 짝 비교(scripts/compare_courtfix_cv.py) — 사전 등록한 세 기준 통과
      (고등급 미탐 1.4→1.3%, S3 재현율 78.8→85.3%, golden100 고등급 미탐 합 72→43).
⚠ 정정 주체는 사람이 아니라 검출기·규칙이다(사람 서명 아님). 원본은 그대로 둔다.

사용:  python scripts/build_v5_clean_policy.py
"""
from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "scripts"))
from build_p1_v5_clean import is_public_ruling  # noqa: E402

SRC = POC / "datasets" / "labeled_p1_v5_clean"
DST = POC / "datasets" / "labeled_p1_v5_clean_policy"
R1 = "llm_judge_primary 가 규칙 판정 S3 를 덮어씀(증시 시황·산업 통계·IR 후기·공개 조사 요약) → S3"
R2 = "공개 판결문(현재 검출기 is_public_ruling 적중)은 비공지성 실패라 S3 — 옛 검출기가 놓쳐 TS/S1/S2 로 남아 있었음"


def sha256_lf(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def main() -> int:
    DST.mkdir(parents=True, exist_ok=True)
    manifest = {"source_dir": "datasets/labeled_p1_v5_clean", "status": "CANDIDATE — 배포본 아님",
                "rules": {"1": R1, "2": R2}, "splits": {}}
    tot = collections.Counter()
    for sp in ("train", "val", "test"):
        src = SRC / f"{sp}.jsonl"
        rows = [json.loads(x) for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
        before = collections.Counter(r["label"] for r in rows)
        ch = {"rule1": [], "rule2": []}
        for r in rows:
            old = r["label"]
            if r.get("label_source") == "llm_judge_primary" and r.get("rule_grade") == "S3" and old != "S3":
                r["label_before_correction_2026_09_20"], r["label_correction_reason"], r["label"] = old, R1, "S3"
                ch["rule1"].append((r.get("doc_id"), old))
            elif is_public_ruling(r) and old != "S3":
                r["label_before_correction_2026_09_20"], r["label_correction_reason"], r["label"] = old, R2, "S3"
                ch["rule2"].append((r.get("doc_id"), old))
        out = DST / f"{sp}.jsonl"
        with out.open("w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        after = collections.Counter(r["label"] for r in rows)
        manifest["splits"][sp] = {
            "rows": len(rows), "rule1_changed": len(ch["rule1"]), "rule2_changed": len(ch["rule2"]),
            "rule2_from": dict(collections.Counter(o for _, o in ch["rule2"])),
            "label_before": dict(before), "label_after": dict(after),
            "source_sha256_lf": sha256_lf(src), "out_sha256_lf": sha256_lf(out)}
        tot["r1"] += len(ch["rule1"])
        tot["r2"] += len(ch["rule2"])
        print(f"{sp}: {len(rows)}행 · 규칙1 {len(ch['rule1'])} · 규칙2(판결문) {len(ch['rule2'])} {dict(collections.Counter(o for _, o in ch['rule2']))}")
    manifest["totals"] = {"rule1": tot["r1"], "rule2": tot["r2"]}
    with (DST / "manifest.json").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"합계: 규칙1 {tot['r1']} + 규칙2 {tot['r2']} = {tot['r1'] + tot['r2']}행 정정")
    return 0


if __name__ == "__main__":
    sys.exit(main())

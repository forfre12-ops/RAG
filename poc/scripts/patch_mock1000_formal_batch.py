"""mock1000 배치(expert_review_mock1000_20261002)의 일부 문서를 다른 문서로 교체한다.

[2026-10-07] 사용자 지적: 배치 1,001건 중 405건(회의 메모·메신저 대화·이메일 형식)이
"문서"로 보기 힘들다 — 계약서·보고서·설계 사양서 같은 공식 문서 형식으로 교체하기로 함.

이 스크립트는 전체 재조립(build_mock1000_final_20261002.py)을 다시 돌리지 않는다 — 그
스크립트는 TEMP 의 임시 입력 파일·중복 치환 큐에 의존하고, 이미 배포된 review_id(MK-####)
번호를 흔들 위험이 있다. 대신 **최종 산출물(internal_manifest.jsonl·reviewer_documents.jsonl·
readable_*/*.txt)을 review_id 단위로 직접 패치**한다 — department·true_grade·info_type은
그대로 두고(분포 불변) title·body·doc_format만 바꾼다.

교체 전 기존 generator 의 안전게이트(등급 토큰 노출·자기기밀선언·PII)를 그대로 재사용해
새 본문도 검사한다 — 이 게이트를 통과하지 못하면 배치 자체를 중단한다(어느 것도 쓰지 않음).

사용:
  python scripts/patch_mock1000_formal_batch.py <batch.json> [--dry-run]

batch.json 형식: [{"review_id","department","grade","info_type","doc_format","title","body"}, ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 콘솔 출구를 UTF-8 로 고정한다 — cp949 콘솔에서 em dash 등에 죽던 것을 막는다.
# 정본은 scripts/_cli_io.py 한 곳이다.
try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from koipa.services.synth_quality import (  # noqa: E402
    _contains_pii,
    _exposes_grade_token,
    _exposes_self_grade_declaration,
)

REPO = Path("F:/antigravity/rag")
OUT = REPO / "poc/datasets/expert_review_mock1000_20261002"
GUIDE40 = REPO / "poc/datasets/expert_review_mock_guide40_20260929"


def load_catalog() -> dict[tuple[str, str], set[str]]:
    catalog: dict[tuple[str, str], set[str]] = {}
    with (GUIDE40 / "internal_manifest.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            catalog.setdefault((r["department"], r["true_grade"]), set()).add(r["info_type"])
    return catalog


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with args.batch.open(encoding="utf-8") as f:
        batch = json.load(f)

    # --- 1) 대상 review_id가 실제로 이번에 대체하기로 한 405건(메모·메신저·이메일)인지 확인 ---
    manifest_path = OUT / "internal_manifest.jsonl"
    manifest_rows: dict[str, dict] = {}
    manifest_order: list[str] = []
    with manifest_path.open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            manifest_rows[d["review_id"]] = d
            manifest_order.append(d["review_id"])

    informal = {"회의 메모", "메신저 대화", "이메일"}
    catalog = load_catalog()

    errs: list[str] = []
    for item in batch:
        rid = item["review_id"]
        if rid not in manifest_rows:
            errs.append(f"{rid}: manifest에 없는 review_id")
            continue
        cur = manifest_rows[rid]
        if cur["doc_format"] not in informal:
            errs.append(f"{rid}: 대체 대상(405건)이 아님 — 현재 doc_format={cur['doc_format']!r}")
        if cur["department"] != item["department"] or cur["true_grade"] != item["grade"]:
            errs.append(
                f"{rid}: department/grade가 원본과 다르다 "
                f"(원본 {cur['department']}/{cur['true_grade']} vs 새 {item['department']}/{item['grade']})"
            )
        if item["info_type"] not in catalog.get((item["department"], item["grade"]), set()):
            errs.append(f"{rid}: info_type {item['info_type']!r} 이 guide40 카탈로그에 없다")
        full = item["title"] + "\n" + item["body"]
        if _exposes_grade_token(full):
            errs.append(f"{rid}: 게이트 위반 — 등급 토큰 노출")
        if _exposes_self_grade_declaration(full):
            errs.append(f"{rid}: 게이트 위반 — 자기기밀선언")
        if _contains_pii(full):
            errs.append(f"{rid}: 게이트 위반 — PII")

    if errs:
        print(f"[patch_mock1000] 검증 실패 {len(errs)}건 — 아무것도 바꾸지 않았다")
        for e in errs:
            print("  -", e)
        return 1

    print(f"[patch_mock1000] 검증 통과 — {len(batch)}건 전부 게이트·카탈로그 일치")

    if args.dry_run:
        print("--dry-run: 파일은 쓰지 않았다")
        return 0

    # --- 2) manifest 패치 (review_id 순서 보존) ---
    by_id = {item["review_id"]: item for item in batch}
    new_manifest_lines = []
    for rid in manifest_order:
        if rid in by_id:
            item = by_id[rid]
            row = {**manifest_rows[rid]}
            row["doc_format"] = item["doc_format"]
            row["char_count"] = len(item["body"])
            row["source"] = "formal_doc_replacement_2026-10-07"
            row["label_basis"] = row["label_basis"] + " (원 메모/이메일/메신저 형식을 공식 문서로 교체, 2026-10-07)"
            new_manifest_lines.append(json.dumps(row, ensure_ascii=False))
        else:
            new_manifest_lines.append(json.dumps(manifest_rows[rid], ensure_ascii=False))
    manifest_path.write_text("\n".join(new_manifest_lines) + "\n", encoding="utf-8")

    # --- 3) reviewer_documents.jsonl 패치 ---
    reviewer_path = OUT / "reviewer_documents.jsonl"
    reviewer_rows: dict[str, dict] = {}
    reviewer_order: list[str] = []
    with reviewer_path.open(encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            reviewer_rows[d["review_id"]] = d
            reviewer_order.append(d["review_id"])
    new_reviewer_lines = []
    for rid in reviewer_order:
        if rid in by_id:
            item = by_id[rid]
            text = item["title"] + "\n\n" + item["body"]
            new_reviewer_lines.append(json.dumps({"review_id": rid, "text": text}, ensure_ascii=False))
        else:
            new_reviewer_lines.append(json.dumps(reviewer_rows[rid], ensure_ascii=False))
    reviewer_path.write_text("\n".join(new_reviewer_lines) + "\n", encoding="utf-8")

    # --- 4) readable_*/*.txt 패치 ---
    for item in batch:
        rid = item["review_id"]
        grade = item["grade"]
        text = item["title"] + "\n\n" + item["body"]
        (OUT / "readable_reviewer" / f"{rid}.txt").write_text(text, encoding="utf-8")
        header = (
            "[내부 전용 — 검수자 배포 금지]\n"
            f"review_id: {rid}\n"
            f"true_grade: {grade}\n"
            f"department: {item['department']} / info_type: {item['info_type']} / doc_format: {item['doc_format']}\n"
            "source_type: internal / security_marking: none / access_scope: none\n"
            "label_basis: 공식 문서 형식으로 교체(2026-10-07)\n"
            + "=" * 60 + "\n\n"
        )
        (OUT / "readable_internal" / f"{rid}_{grade}.txt").write_text(header + text, encoding="utf-8")

    print(f"[patch_mock1000] {len(batch)}건 교체 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

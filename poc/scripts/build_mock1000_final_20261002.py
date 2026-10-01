"""1,001건 모의문서 최종 병합 + guide40 형식 폴더 생성 (2026-10-02).

입력:
  - poc/reports/mock1000_v2_docs.json           (934건, 메인 워크플로 v2)
  - %TEMP%/hr_s3_batch_a5b7.json  (인사/S3 18건, 보충 3차)
  - %TEMP%/hr_s3_batch_a9fb.json  (인사/S3 18건, 보충 5차)
  - %TEMP%/mgmt_s2_batch_1.json   (경영/S2 15건, 보충 1차)
  - %TEMP%/mgmt_s2_batch_2.json   (경영/S2 16건, 보충 2차)

출력: poc/datasets/expert_review_mock1000_20261002/ (덮어쓰기)
  - internal_manifest.jsonl
  - reviewer_documents.jsonl
  - readable_reviewer/MK-####.txt
  - readable_internal/MK-####_<grade>.txt
  - README_읽어주십시오.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from koipa.services.synth_quality import (  # noqa: E402
    _contains_pii,
    _exposes_grade_token,
    _exposes_self_grade_declaration,
)

REPO = Path("F:/antigravity/rag")
TEMP = Path("C:/Users/tio/AppData/Local/Temp")
OUT = REPO / "poc/datasets/expert_review_mock1000_20261002"
GUIDE40 = REPO / "poc/datasets/expert_review_mock_guide40_20260929"

TARGET_GRADE = {"TS": 48, "S1": 188, "S2": 437, "S3": 328}
TARGET_DEPT = {
    "경영": 141, "구매": 47, "생산제조": 204, "연구개발": 156,
    "인사": 156, "총무": 93, "판매": 110, "회계": 94,
}


def load_tagged(path: Path, department: str, grade: str) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    docs = data["docs"] if isinstance(data, dict) else data
    out = []
    for d in docs:
        out.append({
            "department": department,
            "grade": grade,
            "info_type": d["info_type"],
            "title": d["title"].strip(),
            "body": d["body"].strip(),
        })
    return out


def doc_format_guess(body: str) -> str:
    head = body[:40]
    if "[" in body[:10] or "]\n" in body[:200]:
        return "메신저 대화"
    if head.startswith("일시:") or "참석:" in body[:60]:
        return "회의 메모"
    if head.startswith("기안부서:") or "결과\n" in body:
        return "품의서"
    if "받는사람" in body[:60] or "받는 사람" in body[:60] or "보낸사람" in body[:60] or "보낸 사람" in body[:60]:
        return "이메일"
    if head.startswith("점검일:") or "점검자:" in body[:60]:
        return "점검 기록"
    return "업무 기록"


def main() -> None:
    main_docs = []
    with (REPO / "poc/reports/mock1000_v2_docs.json").open(encoding="utf-8") as f:
        for d in json.load(f):
            main_docs.append({
                "department": d["department"],
                "grade": d["grade"],
                "info_type": d["info_type"],
                "title": d["title"].strip(),
                "body": d["body"].strip(),
            })

    backfill = []
    backfill += load_tagged(TEMP / "hr_s3_batch_a5b7.json", "인사", "S3")
    backfill += load_tagged(TEMP / "hr_s3_batch_a9fb.json", "인사", "S3")
    backfill += load_tagged(TEMP / "mgmt_s2_batch_1.json", "경영", "S2")
    backfill += load_tagged(TEMP / "mgmt_s2_batch_2.json", "경영", "S2")

    all_docs = main_docs + backfill
    print(f"total docs: {len(all_docs)} (main {len(main_docs)} + backfill {len(backfill)})")

    # --- 분포 검증 ---
    from collections import Counter
    by_grade = Counter(d["grade"] for d in all_docs)
    by_dept = Counter(d["department"] for d in all_docs)
    print("grade:", dict(by_grade))
    print("dept:", dict(by_dept))
    assert dict(by_grade) == TARGET_GRADE, f"grade mismatch: {dict(by_grade)} vs {TARGET_GRADE}"
    assert dict(by_dept) == TARGET_DEPT, f"dept mismatch: {dict(by_dept)} vs {TARGET_DEPT}"
    assert len(all_docs) == 1001, f"total mismatch: {len(all_docs)}"

    # --- 길이 통계 ---
    lens = [len(d["body"]) for d in all_docs]
    print(f"len min/mean/median/max: {min(lens)} / {sum(lens)/len(lens):.1f} / "
          f"{sorted(lens)[len(lens)//2]} / {max(lens)}")

    # --- 게이트 재검사 ---
    violations = []
    for i, d in enumerate(all_docs):
        full = d["title"] + "\n" + d["body"]
        reasons = []
        if _exposes_grade_token(full):
            reasons.append("grade_token")
        if _exposes_self_grade_declaration(full):
            reasons.append("self_decl")
        if _contains_pii(full):
            reasons.append("pii")
        if reasons:
            violations.append((i, reasons, d["department"], d["grade"], d["title"]))
    print(f"gate violations: {len(violations)}")
    for v in violations[:30]:
        print("  ", v)
    assert not violations, "게이트 위반이 있다 — 배포 전 수정 필요"

    # --- 폴더 재구성 ---
    (OUT / "readable_reviewer").mkdir(parents=True, exist_ok=True)
    (OUT / "readable_internal").mkdir(parents=True, exist_ok=True)
    for old in (OUT / "readable_reviewer").glob("*.txt"):
        old.unlink()
    for old in (OUT / "readable_internal").glob("*.txt"):
        old.unlink()

    manifest_lines = []
    reviewer_lines = []
    for idx, d in enumerate(all_docs, start=1):
        rid = f"MK-{idx:04d}"
        char_count = len(d["body"])
        dfmt = doc_format_guess(d["body"])
        manifest_row = {
            "review_id": rid,
            "true_grade": d["grade"],
            "department": d["department"],
            "info_type": d["info_type"],
            "doc_format": dfmt,
            "char_count": char_count,
            "source": "guide_catalog_2026-10-02_expansion",
            "label_basis": "가이드 카탈로그 표(정보유형->등급) 직접 배정",
            "contrast_of": None,
            "source_type": "internal",
            "security_marking": "none",
            "access_scope": "none",
            "metadata_mismatched": False,
        }
        manifest_lines.append(json.dumps(manifest_row, ensure_ascii=False))

        text = d["title"] + "\n\n" + d["body"]
        reviewer_lines.append(json.dumps({"review_id": rid, "text": text}, ensure_ascii=False))

        (OUT / "readable_reviewer" / f"{rid}.txt").write_text(text, encoding="utf-8")

        header = (
            "[내부 전용 — 검수자 배포 금지]\n"
            f"review_id: {rid}\n"
            f"true_grade: {d['grade']}\n"
            f"department: {d['department']} / info_type: {d['info_type']} / doc_format: {dfmt}\n"
            "source_type: internal / security_marking: none / access_scope: none\n"
            "label_basis: 가이드 카탈로그 표(정보유형->등급) 직접 배정\n"
            + "=" * 60 + "\n\n"
        )
        (OUT / "readable_internal" / f"{rid}_{d['grade']}.txt").write_text(
            header + text, encoding="utf-8"
        )

    (OUT / "internal_manifest.jsonl").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    (OUT / "reviewer_documents.jsonl").write_text("\n".join(reviewer_lines) + "\n", encoding="utf-8")

    print(f"done. wrote {len(all_docs)} docs to {OUT}")
    print(f"readable_reviewer: {len(list((OUT/'readable_reviewer').glob('*.txt')))} files")
    print(f"readable_internal: {len(list((OUT/'readable_internal').glob('*.txt')))} files")


if __name__ == "__main__":
    main()

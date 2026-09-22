#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 생성 문서(factfirst pilot, 9/21)를 골든 후보 풀로 들여온다.

■ 왜 필요한가

검수 콘솔(`ProxyGoldCandidateService`)이 읽는 후보 풀은 `datasets/proxy_gold/
single_document_candidates/`의 `.metadata.json`+본문 파일 쌍이다. `datasets/
mock_final_factfirst_20260921/`(1,000건, 9/21 완성)은 이 형식이 아니라
`reviewer_documents_all.jsonl`(review_id·본문)과 `internal_manifest.jsonl`
(review_id·잠정등급·S/V/M·분할)로 따로 있어, 아직 검수 콘솔에 한 건도 안 올라가 있다
(9/22 재확인: 후보 풀에 mock_final_factfirst 매치 0건). 이 스크립트가 그 간극을 메운다.

■ 무엇을 들여오는가 — 봉인 200건은 절대 건드리지 않는다

`internal_manifest.jsonl`의 `split`은 `train`(646)·`dev_expression`(154)·`sealed`(200).
`datasets/mock_final_factfirst_20260921/README.md`: "봉인 문서는 학습·튜닝·미탐 분석에
쓰지 않는다. 검수 라벨이 확정된 뒤 최종 평가 때 한 번만 연다." → 이 스크립트는 **train +
dev_expression = 800건만** 들여온다. sealed 는 코드에서 하드 제외하고, 들여온 수가
800이 아니면 그 자리에서 멈춘다(조용히 덜 들여오거나 sealed 가 섞이면 알아채기 어렵다).

■ 등급 정보가 API로 새지 않는지 — 기존 코드로 확인한 사실

`ProxyGoldCandidateService._scan()`이 `.metadata.json`에서 API 응답으로 옮기는 필드는
`doc_id·title·proposed_grade·proposed_grade_basis·final_grade·status·document_origin·
requires_manual_audit·review_batch` 등 **정해진 목록뿐**이다(원본 dict를 그대로 반환하지
않는다 — `get_candidate`가 이 rows 중 하나를 돌려줄 뿐). 그래서:
  - `doc_id` 는 review_id(`FD-0001`)를 그대로 쓴다 — 등급 코드가 안 박혀 있어 기존
    풀(988/1,067건이 `GOLD-B1-S1-…`처럼 등급 코드를 담아 별칭 계층이 필요했던 문제)이
    애초에 없다.
  - `grade_rationale`(곱셈표 계산 근거, 내부 감사용) 필드는 `_scan()`이 안 읽는 필드라
    API로 안 나간다 — 다만 안전을 더 얹으려고 "## 등급 제안 사유: <등급>" 같은 등급
    자체가 박힌 문구는 넣지 않는다(기존 배치의 `grade_rationale`엔 있었다).
  - `intended_label`(=proposed_grade)은 기존 합성 후보와 동일하게 넣는다 — 이건
    검수자에게 원래 보여도 되는 값이고, 9/22에 이미 구현된 블라인드 손잡이
    (`golden_review_blind_enforced`)를 켜면 서버가 이 값 자체를 응답에서 뺀다
    (BLIND_HIDDEN_CANDIDATE_FIELDS). 블라인드 검수를 쓰려면 그 손잡이를 켜는 게
    맞다 — 이 스크립트가 라벨을 아예 안 넣는 것은 해법이 아니다(라벨이 없으면
    승격·집계가 안 된다).

■ 사용

    python scripts/import_factfirst_reviewer_docs.py            # 무엇이 들어갈지만 보여준다(기본, 쓰지 않음)
    python scripts/import_factfirst_reviewer_docs.py --commit    # 실제로 파일을 쓴다
    python scripts/import_factfirst_reviewer_docs.py --commit --force   # 이미 있는 review_id 도 덮어쓴다

기존 후보와 review_id 충돌은 없다(기존 doc_id 는 `GOLD-*`·16자리 16진수·업로드
UUID 계열, `FD-` 접두는 이번 배치가 처음이다) — 그래도 실행 시 겹침을 세어 알린다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC / "src"))

SRC_DIR = _POC / "datasets" / "mock_final_factfirst_20260921"
DEST_DIR = _POC / "datasets" / "proxy_gold" / "single_document_candidates"
REVIEWER_DOCS = SRC_DIR / "reviewer_documents_all.jsonl"
MANIFEST = SRC_DIR / "internal_manifest.jsonl"

INCLUDED_SPLITS = frozenset({"train", "dev_expression"})
EXPECTED_TOTAL = 800  # 646 + 154 — 어긋나면 멈춘다(수치는 README.md 9/21 기준)
REVIEW_BATCH = "factfirst_20260921_nonsealed"

_VALID_GRADES = {"TS", "S1", "S2", "S3"}


def _load_jsonl(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            key = row.get("review_id")
            if not key:
                raise ValueError(f"{path.name}:{line_no} review_id 없음")
            if key in out:
                raise ValueError(f"{path.name}:{line_no} review_id 중복: {key}")
            out[key] = row
    return out


def build_rows() -> list[dict]:
    if not REVIEWER_DOCS.is_file() or not MANIFEST.is_file():
        raise SystemExit(f"입력 파일이 없다: {REVIEWER_DOCS} / {MANIFEST}")
    docs = _load_jsonl(REVIEWER_DOCS)
    manifest = _load_jsonl(MANIFEST)
    missing_manifest = set(docs) - set(manifest)
    if missing_manifest:
        raise SystemExit(f"본문은 있는데 manifest 가 없는 review_id {len(missing_manifest)}건: "
                          f"{sorted(missing_manifest)[:5]}...")

    rows = []
    sealed_count = 0
    for review_id, doc in docs.items():
        m = manifest[review_id]
        split = m.get("split")
        if split == "sealed":
            sealed_count += 1
            continue
        if split not in INCLUDED_SPLITS:
            raise SystemExit(f"모르는 split 값: {split!r} ({review_id}) — 코드를 먼저 확인할 것")
        grade = m.get("grade")
        if grade not in _VALID_GRADES:
            raise SystemExit(f"모르는 등급 값: {grade!r} ({review_id})")
        text = doc.get("text") or ""
        if not text.strip():
            raise SystemExit(f"본문이 비어 있음: {review_id}")
        rows.append({
            "review_id": review_id,
            "text": text,
            "grade": grade,
            "S": m.get("S"), "V": m.get("V"), "M": m.get("M"),
            "domain": m.get("domain"),
            "form": m.get("form"),
            "round": m.get("round"),
            "family_id": m.get("family_id"),
            "split": split,
        })
    if sealed_count != 200:
        raise SystemExit(f"봉인 문서 수가 예상(200)과 다르다: {sealed_count} — 무엇이 바뀌었는지 먼저 확인할 것")
    if len(rows) != EXPECTED_TOTAL:
        raise SystemExit(f"들여올 문서 수가 예상({EXPECTED_TOTAL})과 다르다: {len(rows)}")
    return rows


def write_candidate(row: dict, *, force: bool) -> str:
    """반환값: 'written' | 'skipped_exists'."""
    doc_id = row["review_id"]
    meta_path = DEST_DIR / f"{doc_id}.metadata.json"
    md_path = DEST_DIR / f"{doc_id}.cleaned.md"
    if meta_path.exists() and not force:
        return "skipped_exists"

    grade_rationale = (
        f"S×V×M = {row['S']}×{row['V']}×{row['M']} (가이드 12쪽 곱셈표 계산값). "
        "산식 미확정 조합 포함 가능 — 발주처 결정(D01~D08) 전까지 잠정 라벨이다."
    )
    meta = {
        "doc_id": doc_id,
        "intended_label": row["grade"],
        "document_origin": "synthetic",
        "document_type": row["form"] or "미상",
        "authoring_method": "factfirst_docgen_pilot_20260921",
        "requires_manual_audit": True,
        "candidate_status": "proposed",
        "claim_scope": (
            "사실 우선 생성 문서(factfirst pilot). 라벨은 같은 AI 판정 재현으로 검증됐으나 "
            "사람 확정 전이다 — Proxy Gold 이며 Locked Gold 근거가 아니다."
        ),
        "domain": row["domain"],
        "source_reference": f"factfirst_docgen round={row['round']} family={row['family_id']} split={row['split']}",
        "content_revision_path": f"{doc_id}.cleaned.md",
        "content_revision_note": "9/22 가져오기 원본 그대로(별도 세척 없음 — 원본이 이미 정답 노출 없음, README 참고).",
        "import_note": "poc/scripts/import_factfirst_reviewer_docs.py 로 datasets/mock_final_factfirst_20260921 에서 가져옴.",
        "grade_rationale": grade_rationale,
        "review_batch": REVIEW_BATCH,
        "review_batch_note": (
            "9/21 사실 우선 생성 1,000건 중 봉인 200건을 뺀 800건(train 646 + dev_expression 154). "
            "봉인분은 최종 평가 때 별도로만 연다 — 이 배치에 없다."
        ),
    }
    md_path.write_text(row["text"], encoding="utf-8")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return "written"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", action="store_true", help="실제로 파일을 쓴다(기본은 미리보기만)")
    ap.add_argument("--force", action="store_true", help="이미 있는 review_id 도 덮어쓴다")
    args = ap.parse_args()

    rows = build_rows()
    by_grade: dict[str, int] = {}
    for r in rows:
        by_grade[r["grade"]] = by_grade.get(r["grade"], 0) + 1
    print(f"대상 {len(rows)}건 (train+dev_expression, 봉인 200건 제외) — 등급 분포 {by_grade}")
    print(f"review_batch = {REVIEW_BATCH}")

    existing_before = len(list(DEST_DIR.glob("*.metadata.json")))
    print(f"후보 풀 현재 {existing_before}건 (가져오기 전)")

    overlap = sum(1 for r in rows if (DEST_DIR / f"{r['review_id']}.metadata.json").exists())
    if overlap:
        print(f"⚠ 이미 존재하는 review_id {overlap}건 — --force 없이는 건너뛴다")

    if not args.commit:
        print("\n(미리보기만 했다 — 실제로 쓰려면 --commit)")
        return

    counts = {"written": 0, "skipped_exists": 0}
    for row in rows:
        result = write_candidate(row, force=args.force)
        counts[result] += 1

    existing_after = len(list(DEST_DIR.glob("*.metadata.json")))
    print(f"완료: 신규 {counts['written']}건 · 건너뜀(이미 존재) {counts['skipped_exists']}건")
    print(f"후보 풀 {existing_before} → {existing_after}건")


if __name__ == "__main__":
    main()

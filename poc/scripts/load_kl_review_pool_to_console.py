#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검수 후보 풀(_pool_*.jsonl) → 골든셋 관리 콘솔에 적재.

콘솔(ProxyGoldCandidateService)은 `single_document_candidates/` 의 `*.metadata.json` 을 훑고,
본문은 metadata 의 `content_revision_path`(있으면 우선) 또는 `{doc_id}_*.md` 에서 읽는다.
기존 1,000건 중 언더스코어 명명 100건만 보이던 이유가 이 글롭이다.

이 스크립트가 하는 것:
  · 합성 후보 → 정제 본문을 `{doc_id}.cleaned.md` 로 쓰고 metadata 에 content_revision_path 지정
    (원본 .md 는 건드리지 않는다 — 되돌리려면 metadata 의 그 키만 지우면 된다)
  · 실문서 후보 → `{doc_id}_{슬러그}.md` + `{doc_id}.metadata.json` 신규 생성
  · 기존 100건(GOLD-CAND-*)은 그대로 둔다

주의: 실문서(public_real)는 콘솔에서 `approve` 가 막혀 있고 `change`(사유 필수)로만 등급을
확정할 수 있다. 합성이 아닌 문서에 무인 승인을 허용하지 않으려는 설계다.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSOLE_DIR = ROOT / "datasets" / "proxy_gold" / "single_document_candidates"


def slug(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣._-]+", "", (text or "").strip())
    return (s[:limit] or "document")


def title_of(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if len(line) >= 4:
            return line[:60]
    return "검수 후보 문서"


DROP_H2 = ("확인 질문과 답변 기록", "세부 검토 경과", "후속 조치와 종료 조건")
DROP_H3 = ("검수 전 확인 목록",)
GRADE_TOK = re.compile(r"\b(TS|S1|S2|S3)\b")


def strip_scaffolding(md: str) -> str:
    """검수 스캐폴딩 제거 — '등급 제안 사유' 헤딩과 검수 지시 섹션."""
    out, skip = [], False
    for ln in md.splitlines():
        m2 = re.match(r"^##\s+(?!#)(.*)$", ln)
        if m2:
            t = m2.group(1).strip()
            skip = t.startswith("등급 제안 사유") or any(t.startswith(d) for d in DROP_H2)
            if skip:
                continue
        m3 = re.match(r"^###\s+(.*)$", ln)
        if m3 and any(m3.group(1).strip().startswith(d) for d in DROP_H3):
            skip = True
            continue
        if not skip:
            out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def drop_grade_sentences(text: str) -> str:
    """등급 문자열이 든 문장만 제거 — 검수자가 본문을 읽기 전에 정답을 보지 않도록."""
    kept = []
    for para in text.split("\n"):
        if not para.strip():
            kept.append(para)
            continue
        if para.lstrip().startswith("#"):
            kept.append(GRADE_TOK.sub("", para).rstrip())
            continue
        parts = re.split(r"(?<=다\.)\s+|(?<=[.!?])\s+", para)
        keep = [s for s in parts if s.strip() and not GRADE_TOK.search(s)]
        if keep:
            kept.append(" ".join(keep))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def clean_remaining_synthetic(dry_run: bool) -> dict:
    """풀에 없더라도 콘솔에 보이는 합성 후보 전부에 같은 정제를 적용한다.

    콘솔은 등급을 사람이 정하는 화면이다. 본문에 '등급 제안 사유: TS' 가 남아 있으면
    검수자가 문서를 읽기 전에 답을 보게 되어 검수가 검증이 아니라 확인 절차가 된다.
    """
    stat = {"추가정제": 0, "이미정제": 0, "본문없음": 0, "합성아님": 0}
    for meta_path in sorted(CONSOLE_DIR.glob("*.metadata.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        doc_id = str(meta.get("doc_id") or "")
        if not doc_id:
            continue
        if str(meta.get("document_origin")) != "synthetic":
            stat["합성아님"] += 1
            continue
        rev = str(meta.get("content_revision_path") or "").strip()
        if rev:
            # 이전 개정본이 이미 걸려 있어도 등급이 남아 있으면 다시 정제한다.
            # (실측: revisions/*.v3.md 11건에 `| 검토등급(후보) | S1 / 사람 검수 전 |` 잔존)
            rev_path = (CONSOLE_DIR / rev).resolve()
            if not (rev_path.is_relative_to(CONSOLE_DIR.resolve()) and rev_path.is_file()):
                stat["본문없음"] += 1
                continue
            body = rev_path.read_text(encoding="utf-8")
            if not GRADE_TOK.search(body):
                stat["이미정제"] += 1
                continue
            source_text = body
        else:
            docs = list(CONSOLE_DIR.glob(f"{doc_id}_*.md"))
            if len(docs) != 1:
                stat["본문없음"] += 1
                continue
            source_text = docs[0].read_text(encoding="utf-8")
        cleaned = drop_grade_sentences(strip_scaffolding(source_text))
        if len(cleaned) < 400:
            stat["본문없음"] += 1
            continue
        if not dry_run:
            rev = f"{doc_id}.cleaned.md"
            (CONSOLE_DIR / rev).write_text(cleaned, encoding="utf-8", newline="\n")
            meta["content_revision_path"] = rev
            meta["content_revision_note"] = (
                "검수 스캐폴딩(등급 제안 사유·검수 지시)과 등급 문자열 문장을 제거한 본문. "
                "원본은 같은 폴더의 원 .md 에 보존."
            )
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        stat["추가정제"] += 1
    return stat


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="datasets/golden_review/_pool_20260809.jsonl")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--clean-all-synthetic", action="store_true",
                    help="풀 외 합성 후보에도 같은 정제를 적용(콘솔 전체 정합)")
    args = ap.parse_args(argv)

    pool = Path(args.pool)
    if not pool.is_absolute():
        pool = ROOT / pool
    rows = [json.loads(l) for l in pool.read_text(encoding="utf-8").splitlines() if l.strip()]

    CONSOLE_DIR.mkdir(parents=True, exist_ok=True)
    stat = {"합성_정제본지정": 0, "실문서_신규": 0, "이미적재": 0, "건너뜀": 0}

    for r in rows:
        doc_id = str(r["doc_id"])
        text = r["text"]
        meta_path = CONSOLE_DIR / f"{doc_id}.metadata.json"

        # 파일명이 `{doc_id}_{제목}.md` 인 후보(GOLD-CAND-* 100건)는 풀에 stem 전체가 들어와
        # doc_id 에 제목 접미사가 붙어 있다. 그대로 두면 **이미 콘솔에 있는 합성 문서를 실문서로
        # 새로 등록**해 버린다(2026-08-09 실측 28건). 접미사를 떼고 실제 metadata 를 먼저 찾는다.
        if not meta_path.is_file() and "_" in doc_id:
            base = doc_id.split("_", 1)[0]
            if (CONSOLE_DIR / f"{base}.metadata.json").is_file():
                doc_id = base
                meta_path = CONSOLE_DIR / f"{doc_id}.metadata.json"

        if meta_path.is_file():
            # 합성 후보 — 원본 metadata 를 살리고 정제 본문만 연결한다.
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            rev_name = f"{doc_id}.cleaned.md"
            if meta.get("content_revision_path") == rev_name:
                stat["이미적재"] += 1
                continue
            if not args.dry_run:
                (CONSOLE_DIR / rev_name).write_text(text, encoding="utf-8", newline="\n")
                meta["content_revision_path"] = rev_name
                meta["content_revision_note"] = (
                    "검수 스캐폴딩(등급 제안 사유·검수 지시)과 등급 문자열 문장을 제거한 본문. "
                    "원본은 같은 폴더의 원 .md 에 보존."
                )
                meta["candidate_status"] = meta.get("candidate_status") or "proposed"
                meta_path.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
            stat["합성_정제본지정"] += 1
            continue

        # 실문서 후보 — 콘솔에 없던 문서. 본문 + metadata 신규 생성.
        existing = list(CONSOLE_DIR.glob(f"{doc_id}_*.md"))
        if existing:
            stat["이미적재"] += 1
            continue
        title = title_of(text)
        md_name = f"{doc_id}_{slug(title)}.md"
        meta = {
            "doc_id": doc_id,
            "intended_label": r.get("label"),
            "document_origin": "public_real",
            "document_type": title,
            "authoring_method": "gold_real_import",
            "requires_manual_audit": True,
            "candidate_status": "under_review",
            "source_reference": r.get("source") or "",
            "domain": r.get("domain") or "",
            "claim_scope": (
                "검수 전 후보. 승인되어도 Proxy Gold 이며 Locked Gold·실운영 정확도 근거가 아니다."
            ),
            "import_note": "gold_real 정본에서 가져온 실문서 후보(학습셋 포함분 제외).",
        }
        if not args.dry_run:
            (CONSOLE_DIR / md_name).write_text(text, encoding="utf-8", newline="\n")
            meta_path.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        stat["실문서_신규"] += 1

    extra = clean_remaining_synthetic(args.dry_run) if args.clean_all_synthetic else {}
    print(json.dumps({"pool": str(pool.relative_to(ROOT)), "rows": len(rows),
                      "dry_run": args.dry_run, **stat, **extra},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

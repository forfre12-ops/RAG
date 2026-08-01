#!/usr/bin/env python
"""발주처(지재원) 실문서 골든셋 인테이크 — 블라인드 라벨링 → locked_gold_eval 승격.

배경
----
현재 골든 파일 전체에서 사람 확정 라벨(label_source=human_review)은 1건뿐이고,
평가에 쓰이는 홀드아웃도 전부 기계 라벨(판례 자동매핑·LLM 판정)이다. 기계가 정한
정답으로 기계를 채점하는 순환이라 감리 근거가 되지 못한다. 발주처 담당자가 실문서에
등급을 확정하면 그것이 이 사업 최초의 평가 정답지가 된다.

블라인드가 핵심이다
-------------------
분류 결과를 보고 등급을 정하면 그 답에 끌린다(앵커링). 그 정답지로 정확도를 재면
순환 논리라 "정답을 시스템이 알려준 것 아니냐"에 반박할 수 없다.
**이 스크립트는 분류를 일절 돌리지 않는다.** 텍스트 추출만 하고 등급 칸은 비워 둔다.

두 단계
-------
  1) scan  — 문서 폴더를 훑어 텍스트를 추출하고 기입 시트(CSV)를 낸다. 등급 빈칸.
             담당자는 **원본 문서를 직접 열어 보고** 등급을 판단한다(미리보기는 식별용).
  2) build — 기입된 CSV 를 받아 운영 서명 게이트(promote_to_locked)로 통과시켜
             locked_gold_eval 레코드를 만든다. 기계 계정·등급 누락·미서명은 여기서 거부된다.

사용
----
  # 1단계 — 담당자에게 줄 기입 시트 생성
  python scripts/golden_intake.py scan --docs-dir <문서폴더> --out-dir datasets/gold_real/intake

  # (담당자가 label_sheet.csv 의 등급·판단근거·확정자·확정일 기입)

  # 2단계 — 정답지 생성
  python scripts/golden_intake.py build --intake-dir datasets/gold_real/intake

주의
----
  * 실문서는 발주처 망 안에 머문다. 산출물(JSONL·CSV)에 본문이 담기므로 반출 금지.
  * 평가 정답지는 학습에서 제외해야 한다(train-on-test 방지). build 가 기존 학습셋과
    텍스트 해시를 대조해 겹치는 문서를 자동 제외한다.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

SUPPORTED = {".hwp", ".hwpx", ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".txt", ".md"}
GRADES = ("TS", "S1", "S2", "S3")
PREVIEW_LEN = 180

# 담당자 기입 컬럼 — 순서가 곧 시트 열 순서다.
SHEET_COLS = [
    "doc_id", "파일명", "형식", "추출문자수", "추출경고",
    "등급", "판단근거", "확정자", "확정일",
    "본문미리보기(식별용)",
]

# 추출 경고 코드 → 담당자용 한국어. 시트를 받는 사람은 발주처 실무자라 내부 코드를
# 그대로 노출하면 읽히지 않는다. 미등록 코드는 원문 유지(은폐하지 않음).
WARN_KO = {
    "hwp_table_cells_may_be_missing": "표 셀 일부를 못 읽었을 수 있음",
    "hwp_tables_recovered_by_unhwp": "표 셀을 보조 파서로 회수함",
    "hwp_table_check_unavailable": "표 유무 확인 불가",
    "hwpx_tables_structured": "표를 구조로 인식함(정상)",
    "pdf_tables_may_be_missing": "PDF 표 일부를 못 읽었을 수 있음",
    "ocr_used": "스캔 문서라 OCR 로 읽음",
    "xls_hidden_sheet": "숨김 시트 있음",
    "formula_cache_missing": "엑셀 수식 결과가 비어 있음",
}


def _warn_ko(codes) -> str:
    return " · ".join(WARN_KO.get(c, c) for c in codes)


# 기존 학습셋 — 평가 정답지가 여기 있는 문서와 겹치면 안 된다.
TRAIN_SOURCES = [
    "datasets/gold_real/train_subset.jsonl",
    "datasets/silver_train.jsonl",
    "datasets/gold_real/silver_train_only_nohuman.jsonl",
]


def _text_hash(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def _doc_id(text: str) -> str:
    return _text_hash(text)[:16]


# ────────────────────────────── 1단계: scan ──────────────────────────────

def cmd_scan(docs_dir: Path, out_dir: Path) -> int:
    from lloydk.modules.m2_preprocess.extractor import extract  # noqa: PLC0415

    files = sorted(p for p in docs_dir.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED)
    if not files:
        print(f"[중단] 지원 형식 문서를 찾지 못했다: {docs_dir}")
        print(f"       지원: {', '.join(sorted(SUPPORTED))}")
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    rows, intake = [], []
    failed = 0

    for f in files:
        try:
            r = extract(str(f))
            text = (r.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            text, r, failed = "", None, failed + 1
            print(f"  [추출실패] {f.name}: {type(exc).__name__}")

        if not text:
            failed += 1
            warn = "추출 실패 — 등급 판단은 가능하나 시스템 측정에서 제외될 수 있음"
        else:
            warn = _warn_ko(list(getattr(r, "warnings", None) or []))

        did = _doc_id(text or f.name)
        preview = " ".join((text or "").split())[:PREVIEW_LEN]

        rows.append({
            "doc_id": did,
            "파일명": f.name,
            "형식": f.suffix.lower().lstrip("."),
            "추출문자수": len(text),
            "추출경고": warn,
            "등급": "",           # ← 담당자 기입 (블라인드: 시스템 예측 없음)
            "판단근거": "",        # ← 담당자 기입
            "확정자": "",          # ← 담당자 기입 (실계정)
            "확정일": "",          # ← 담당자 기입 (YYYY-MM-DD)
            "본문미리보기(식별용)": preview,
        })
        intake.append({
            "doc_id": did,
            "text": text,
            "source": f.name,
            "domain": "",
            "format": f.suffix.lower().lstrip("."),
            "text_sha256": _text_hash(text),
            "extract_method": getattr(r, "method", "") if r else "",
            "extract_quality": getattr(r, "quality", 0.0) if r else 0.0,
            "table_coverage": getattr(r, "table_coverage", None) if r else None,
        })

    # 중복 문서(동일 텍스트) 경고 — 같은 문서를 두 번 세면 측정이 왜곡된다.
    seen: dict[str, str] = {}
    dups = []
    for row in rows:
        prev = seen.get(row["doc_id"])
        if prev:
            dups.append((prev, row["파일명"]))
        else:
            seen[row["doc_id"]] = row["파일명"]

    intake_path = out_dir / "intake.jsonl"
    intake_path.write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in intake) + "\n", encoding="utf-8")

    sheet_path = out_dir / "label_sheet.csv"
    # utf-8-sig: Windows Excel 이 BOM 없으면 한글을 cp949 로 오독한다.
    with sheet_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=SHEET_COLS)
        w.writeheader()
        w.writerows(rows)

    (out_dir / "기입안내.txt").write_text(_GUIDE, encoding="utf-8")

    print(f"[scan] 문서 {len(files)}건 · 추출실패 {failed}건")
    if dups:
        print(f"  ⚠ 동일 내용 문서 {len(dups)}쌍 — 중복 계수 주의")
        for a, b in dups[:5]:
            print(f"      {a}  ==  {b}")
    print(f"  기입 시트 : {sheet_path}")
    print(f"  인테이크  : {intake_path}")
    print(f"  안내문    : {out_dir / '기입안내.txt'}")
    print()
    print("  담당자에게 label_sheet.csv 와 기입안내.txt 를 전달하라.")
    print("  ※ 등급은 시스템 분류를 돌리기 전에 원본 문서를 보고 정해야 한다(블라인드).")
    return 0


_GUIDE = """골든셋 등급 기입 안내
=====================

이 시트는 AI 분류 성능을 측정하기 위한 **정답지**입니다.
시스템이 매긴 등급은 들어 있지 않습니다 — 담당자가 정한 값이 정답이 됩니다.

■ 왜 시스템 결과를 안 보여주나요
  시스템 결과를 먼저 보면 그 답에 끌립니다. 그렇게 만든 정답지로 정확도를 재면
  "정답을 시스템이 알려준 것 아니냐"는 지적에 답할 수 없습니다.
  반드시 **분류를 돌리기 전에** 등급을 정해 주십시오.

■ 기입 방법
  1. 파일명으로 원본 문서를 찾아 **직접 열어 보고** 판단하십시오.
     (시트의 '본문미리보기'는 어느 문서인지 식별하기 위한 것일 뿐입니다.)
  2. 아래 4개 칸을 채웁니다.

     등급      : TS / S1 / S2 / S3  중 하나
     판단근거  : 왜 그 등급인지 한 줄. 「영업비밀 등급분류 가이드」의 기준 조항이나
                 문서 내 근거를 적어 주십시오. (감리에서 근거를 묻습니다)
     확정자    : 판단한 분의 실제 계정/성명
     확정일    : YYYY-MM-DD

  3. 판단이 어려운 문서는 **비워 두십시오**. 억지로 채우면 정답지 품질이 떨어집니다.
     비운 행은 자동으로 제외됩니다.

■ 주의
  * '추출경고' 칸에 내용이 있으면 시스템이 그 문서의 일부(예: 표 안 내용)를 못 읽었다는
    뜻입니다. 등급 판단은 원본 기준으로 하시면 되고, 이 표시는 측정 해석에 씁니다.
  * 이 파일에는 문서 본문 일부가 담깁니다. 망 외부로 내보내지 마십시오.
"""


# ────────────────────────────── 2단계: build ──────────────────────────────

def _load_train_hashes() -> set[str]:
    out: set[str] = set()
    for rel in TRAIN_SOURCES:
        p = _POC / rel
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                t = json.loads(line).get("text")
            except Exception:  # noqa: BLE001
                continue
            if t:
                out.add(_text_hash(t))
    return out


def cmd_build(intake_dir: Path, out_path: Path | None, dual_for_upper: bool) -> int:
    from lloydk.golden_signoff import Signoff, promote_to_locked  # noqa: PLC0415

    intake_path = intake_dir / "intake.jsonl"
    sheet_path = intake_dir / "label_sheet.csv"
    for p in (intake_path, sheet_path):
        if not p.exists():
            print(f"[중단] 없음: {p}")
            return 2

    intake = {}
    for line in intake_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            intake[d["doc_id"]] = d

    with sheet_path.open(encoding="utf-8-sig", newline="") as fh:
        sheet = list(csv.DictReader(fh))

    candidates: list[dict] = []
    signoffs: list[Signoff] = []
    skipped: list[tuple[str, str]] = []

    train_hashes = _load_train_hashes()

    for row in sheet:
        did = (row.get("doc_id") or "").strip()
        name = (row.get("파일명") or did).strip()
        grade = (row.get("등급") or "").strip().upper()
        reason = (row.get("판단근거") or "").strip()
        who = (row.get("확정자") or "").strip()
        when = (row.get("확정일") or "").strip()

        if not grade:
            skipped.append((name, "등급 미기입"))
            continue
        if grade not in GRADES:
            skipped.append((name, f"등급 값 오류: {grade}"))
            continue
        if not who:
            skipped.append((name, "확정자 미기입"))
            continue
        if not reason:
            skipped.append((name, "판단근거 미기입"))
            continue
        d = intake.get(did)
        if not d:
            skipped.append((name, "intake 에 없는 doc_id"))
            continue
        if not (d.get("text") or "").strip():
            skipped.append((name, "본문 추출 실패 — 측정 불가"))
            continue
        if d["text_sha256"] in train_hashes:
            skipped.append((name, "학습셋과 동일 문서 — 평가셋 자격 없음(train-on-test)"))
            continue

        candidates.append({
            "doc_id": did, "text": d["text"], "source": d["source"],
            "domain": d.get("domain", ""), "label": grade,
            "origin": "customer_real", "format": d.get("format", ""),
            "text_sha256": d["text_sha256"],
        })
        signoffs.append(Signoff(
            doc_id=did, reviewer_id=who, grade=grade,
            signed_at=when or dt.date.today().isoformat(), note=reason,
        ))

    if not candidates:
        print("[중단] 유효한 기입 행이 없다.")
        for n, r in skipped[:20]:
            print(f"    - {n}: {r}")
        return 2

    # 운영 서명 게이트 그대로 통과시킨다 — 기계 계정·불일치·미서명은 여기서 거부.
    res = promote_to_locked(candidates, signoffs, dual_for_upper=dual_for_upper)

    out = out_path or (intake_dir / "locked_gold_eval.jsonl")
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in res.locked) + "\n", encoding="utf-8")

    print(f"[build] 시트 {len(sheet)}행")
    print(f"  승격(locked_gold_eval) : {len(res.locked)}건  {res.stats.get('locked_by_grade')}")
    print(f"  게이트 거부             : {len(res.rejected)}건  {res.stats.get('rejected_reasons')}")
    print(f"  사전 제외               : {len(skipped)}건")
    for n, r in skipped[:15]:
        print(f"      - {n}: {r}")
    if len(skipped) > 15:
        print(f"      ... 외 {len(skipped) - 15}건")
    print()
    print(f"  정답지: {out}")
    print()
    print("  다음: 이 정답지로 대조 측정")
    print("    python scripts/run_acceptance.py --mode inproc   # 판정 = severity FLOOR")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="문서 폴더 → 기입 시트(CSV) + intake.jsonl")
    s.add_argument("--docs-dir", required=True)
    s.add_argument("--out-dir", default="datasets/gold_real/intake")

    b = sub.add_parser("build", help="기입된 CSV → locked_gold_eval 정답지")
    b.add_argument("--intake-dir", default="datasets/gold_real/intake")
    b.add_argument("--out")
    b.add_argument("--dual-for-upper", action="store_true",
                   help="TS/S1 은 독립 검수자 2인 서명 요구")

    a = ap.parse_args()
    if a.cmd == "scan":
        return cmd_scan(Path(a.docs_dir), Path(a.out_dir))
    return cmd_build(Path(a.intake_dir), Path(a.out) if a.out else None, a.dual_for_upper)


if __name__ == "__main__":
    raise SystemExit(main())

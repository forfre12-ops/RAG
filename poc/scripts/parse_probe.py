"""파싱→검수게이트→분류 E2E 프로브 CLI — 실문서를 넣고 전 구간을 눈으로 본다.

test_e2e_parse_classify.py 가 회귀를 *자동*으로 잠근다면, 이 툴은 운영/시연에서 *수동*으로
"이 문서(들)가 어떻게 파싱됐고, 어떤 게이트를 거쳐, 어떤 등급이 됐는지" 를 즉석 확인한다.
DB/스토리지/임베더 불요 — in-process(rule-fallback 또는 배포 모델)로 전 구간이 돈다.

실행:
  cd poc
  # 단일 파일
  PYTHONPATH=src .venv/Scripts/python.exe scripts/parse_probe.py --path some.hwp
  # 폴더 전체(재귀)
  PYTHONPATH=src .venv/Scripts/python.exe scripts/parse_probe.py --path datasets/gold/formats/docx
  # 기본(gold 코퍼스) + JSON 저장 + golden 라벨 대조
  PYTHONPATH=src .venv/Scripts/python.exe scripts/parse_probe.py --gold --json out.json --compare
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_POC = Path(__file__).resolve().parents[1]
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))

# 인프라 없이 in-process — 벡터/임베더 폴백 중화(테스트 conftest와 동일 취지)
import os  # noqa: E402

os.environ.setdefault("VECTOR_BACKEND", "inmemory")
os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
os.environ.setdefault("TESTING", "1")

from lloydk.config import settings  # noqa: E402
from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline  # noqa: E402
from lloydk.schemas.classify import ClassifyRequest  # noqa: E402
from lloydk.services.classify_service import ClassifyService  # noqa: E402
from lloydk.services.document_ingestion_service import (  # noqa: E402
    extraction_review_decision,
)

_GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}
_SUPPORTED = {
    "txt", "md", "log", "csv", "hwp", "hwpx", "docx", "doc",
    "xlsx", "xlsm", "xls", "pptx", "pptm", "pdf",
    "jpg", "jpeg", "png", "tiff", "tif", "bmp", "webp",
}


def _expected_grade(stem: str) -> str | None:
    parts = stem.split("-")
    if len(parts) >= 3 and parts[0] == "G50" and parts[1] in _GRADE_ORDER:
        return parts[1]
    return None


def _gather(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(
            p for p in path.rglob("*")
            if p.is_file() and p.suffix.lower().lstrip(".") in _SUPPORTED
        )
    return []


def _review(pre):
    ex = pre.extraction
    return extraction_review_decision(
        quality=ex.quality,
        ocr_used=ex.ocr_used,
        error=ex.error,
        min_quality=float(getattr(settings, "extraction_min_quality", 0.6)),
        ocr_requires_review=bool(getattr(settings, "extraction_ocr_requires_review", True)),
        content_quality=pre.quality,
        table_coverage=getattr(ex, "table_coverage", None),
    )


def probe_one(pipe, svc, path: Path, do_classify: bool) -> dict:
    row: dict = {"file": path.name, "fmt": path.suffix.lower().lstrip(".")}
    try:
        pre = pipe.run_file(path)
    except Exception as e:  # noqa: BLE001
        row["stage"] = "PARSE_FAIL"
        row["error"] = f"{type(e).__name__}: {e}"
        return row
    ex = pre.extraction
    row.update(
        method=ex.method,
        ex_quality=round(ex.quality, 3),
        content_quality=round(pre.quality, 3),
        ocr=ex.ocr_used,
        chars=len(pre.text),
        chunks=len(pre.chunks),
        table_cov=getattr(ex, "table_coverage", None),
        ex_error=(ex.error or "")[:80],
        pii=sum(pre.pii_counts.values()) if pre.pii_counts else 0,
    )
    dec = _review(pre)
    row["needs_review"] = dec.requires_review
    row["review_reasons"] = list(dec.reasons)
    if len(pre.text.strip()) == 0:
        row["stage"] = "EMPTY_TEXT"
        return row
    if not do_classify:
        row["stage"] = "PARSED"
        return row
    try:
        resp = svc.classify(ClassifyRequest(doc_id=path.stem, content=pre.text, return_evidence=False))
        row["stage"] = "CLASSIFIED"
        row["label"] = resp.label.value if hasattr(resp.label, "value") else str(resp.label)
        row["conf"] = round(resp.confidence, 3)
        row["status"] = resp.status
        row["model"] = resp.model_version
        row["warnings"] = list(resp.warnings)
    except Exception as e:  # noqa: BLE001
        row["stage"] = "CLASSIFY_FAIL"
        row["error"] = f"{type(e).__name__}: {e}"
    exp = _expected_grade(path.stem)
    if exp:
        row["expected"] = exp
        pred = row.get("label")
        row["match"] = pred == exp
        if pred in _GRADE_ORDER and exp in _GRADE_ORDER:
            row["undergrade"] = _GRADE_ORDER[pred] > _GRADE_ORDER[exp]
    return row


def _print_row(r: dict) -> None:
    stage = r.get("stage", "?")
    head = f"{r['file'][:34]:<34} [{stage:<11}]"
    if stage in ("PARSE_FAIL", "CLASSIFY_FAIL"):
        print(f"{head} {r.get('error','')}")
        return
    gate = "REVIEW:" + ",".join(r.get("review_reasons", [])) if r.get("needs_review") else "auto"
    line = (f"{head} {r.get('method',''):<7} q={r.get('ex_quality','?')}/{r.get('content_quality','?')} "
            f"chars={r.get('chars','?'):<5} chunks={r.get('chunks','?'):<2} "
            f"tbl={r.get('table_cov') or '-':<10} pii={r.get('pii',0):<2} {gate}")
    if "label" in r:
        line += f"  → {r['label']} conf={r.get('conf')} [{r.get('status')}]"
        if "expected" in r:
            mark = "OK" if r.get("match") else ("UNDER!" if r.get("undergrade") else "over")
            line += f" exp={r['expected']}({mark})"
    print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="파싱→검수게이트→분류 E2E 프로브")
    ap.add_argument("--path", type=str, help="파일 또는 폴더(재귀)")
    ap.add_argument("--gold", action="store_true", help="gold 코퍼스(datasets/gold/formats) 대상")
    ap.add_argument("--no-classify", action="store_true", help="파싱+게이트까지만(분류 생략)")
    ap.add_argument("--json", type=str, default=None, help="결과 JSON 저장 경로")
    ap.add_argument("--limit", type=int, default=0, help="처리 파일 수 상한(0=전체)")
    args = ap.parse_args()

    targets: list[Path] = []
    if args.gold:
        for sub in ("formats/docx", "formats/pdf"):
            d = _POC / "datasets" / "gold" / sub
            if d.exists():
                targets += sorted(d.glob("G50-*.*"))
    if args.path:
        targets += _gather(Path(args.path))
    if not targets:
        print("대상 없음 — --path 또는 --gold 지정. (예: --gold, --path datasets/gold/formats/docx)")
        return 2
    if args.limit:
        targets = targets[: args.limit]

    pipe = PreprocessPipeline()
    svc = None if args.no_classify else ClassifyService()
    do_classify = not args.no_classify

    rows = []
    for p in targets:
        r = probe_one(pipe, svc, p, do_classify)
        rows.append(r)
        _print_row(r)

    # ---- 요약 ----
    print("\n" + "=" * 72)
    print(f"SUMMARY  ({len(rows)} files)")
    print("=" * 72)
    stages = Counter(r.get("stage", "?") for r in rows)
    print(f"stages           : {dict(stages)}")
    print(f"by format        : {dict(Counter(r['fmt'] for r in rows))}")
    review = [r for r in rows if r.get("needs_review")]
    rc = Counter()
    for r in review:
        for reason in r.get("review_reasons", []):
            rc[reason] += 1
    print(f"needs_review     : {len(review)}  reasons={dict(rc)}")
    labeled = [r for r in rows if r.get("expected")]
    if labeled:
        match = sum(1 for r in labeled if r.get("match"))
        under = [r for r in labeled if r.get("undergrade")]
        print(f"golden-labeled   : {len(labeled)}  exact-match={match} "
              f"({100*match/len(labeled):.1f}%)  undergrade(FNR-risk)={len(under)}")
        conf = defaultdict(Counter)
        for r in labeled:
            conf[r["expected"]][r.get("label", "?")] += 1
        for g in ["TS", "S1", "S2", "S3"]:
            if g in conf:
                print(f"    {g:>3} -> {dict(conf[g])}")
        for r in under:
            print(f"    UNDERGRADE {r['file']}: exp={r['expected']} pred={r.get('label')}")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 저장: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

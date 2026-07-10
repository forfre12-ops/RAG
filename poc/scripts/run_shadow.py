"""W4 in-place 섀도우런 — 실문서를 폐쇄망 안에서 분류하고 **집계 지표만** 반출.

목적: 고객 폐쇄망의 실문서 텍스처에 대한 시스템 거동을 파일럿이 관측하되, 원문·파일명·per-doc
식별자는 절대 밖으로 나가지 않게 한다("집계만 반출"). 실데이터·검수·반출 0 방침과 정합.

★ 무반출 계약(hard): 반출 JSON 은 aggregate_shadow() 산출물뿐이며, 이 함수는 문서 텍스트·파일명을
  애초에 인자로 받지 않는다(등급·상태·confidence·parse_ok·latency·rule_fallback 의 수치 통계만).
  → 구조적으로 원문이 출력에 샐 수 없다. per-doc 레코드도 출력하지 않는다.

주의: 실문서는 라벨(정답)이 없다 → 진짜 미탐(FNR)은 측정 불가. 본 스크립트는 '거동 분포'
(등급 분포·escalation율·고등급 검수 라우팅율·confidence·지연·룰폴백율)만 낸다. 정확도 판정 아님.

사용(폐쇄망 오퍼레이터): python scripts/run_shadow.py --docs-dir /path/to/real_docs --out reports/shadow_aggregate.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent

_HIGH = {"TS", "S1"}
_DEFAULT_EXTS = (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".txt", ".hwp", ".hwpx")


def _pct(nums: list[float], q: float) -> float:
    if not nums:
        return 0.0
    s = sorted(nums)
    if len(s) == 1:
        return round(s[0], 1)
    k = int(round(q * (len(s) - 1)))
    return round(s[k], 1)


def aggregate_shadow(stats: list[dict]) -> dict:
    """per-doc **수치 통계**(텍스트·파일명 없음) → 집계 지표. 무반출 계약의 경계 함수.

    stats item keys: grade(str|None), status(str), confidence(float), parse_ok(bool),
                     latency_ms(float|None), rule_fallback(bool). ← 원문/식별자 없음.
    """
    n = len(stats)
    if n == 0:
        return {"n": 0, "note": "no documents processed"}

    parse_ok = sum(1 for s in stats if s.get("parse_ok"))
    by_grade: dict[str, int] = {}
    for s in stats:
        g = s.get("grade") or "UNKNOWN"
        by_grade[g] = by_grade.get(g, 0) + 1
    needs_review = sum(1 for s in stats if s.get("status") == "needs_review")
    high_pred = [s for s in stats if (s.get("grade") in _HIGH)]
    high_review = sum(1 for s in high_pred if s.get("status") == "needs_review")
    confs = [float(s.get("confidence") or 0.0) for s in stats]
    low_conf = sum(1 for c in confs if c < 0.7)
    lats = [float(s["latency_ms"]) for s in stats if s.get("latency_ms") is not None]
    rule_fb = sum(1 for s in stats if s.get("rule_fallback"))

    return {
        "n": n,
        "parse_ok_rate": round(parse_ok / n, 4),
        "by_grade_count": by_grade,
        "by_grade_ratio": {g: round(c / n, 4) for g, c in sorted(by_grade.items())},
        "escalation_rate": round(needs_review / n, 4),
        "high_grade_pred_rate": round(len(high_pred) / n, 4),
        # 안전 프록시: 고등급 예측 중 검수로 라우팅된 비율(불확실 고등급을 자동확정하지 않는가).
        "high_grade_escalation_rate": (round(high_review / len(high_pred), 4) if high_pred else None),
        "confidence_p50": _pct(confs, 0.50),
        "confidence_p95": _pct(confs, 0.95),
        "low_confidence_rate": round(low_conf / n, 4),
        "latency_p50_ms": _pct(lats, 0.50),
        "latency_p95_ms": _pct(lats, 0.95),
        "rule_fallback_rate": round(rule_fb / n, 4),
        "export_contract": "aggregate-only; no document text, filenames, or per-doc records",
    }


def _build_serving():
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    os.environ.setdefault("REQUIRE_REAL_CLASSIFIER", "false")
    os.environ.setdefault("TESTING", "1")
    if str(_POC / "src") not in sys.path:
        sys.path.insert(0, str(_POC / "src"))
    from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline  # noqa: PLC0415
    from lloydk.schemas.classify import ClassifyRequest  # noqa: PLC0415
    from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415

    return PreprocessPipeline(), ClassifyService(), ClassifyRequest


def _iter_docs(docs_dir: Path, exts: tuple[str, ...], max_docs: int):
    seen = 0
    for p in sorted(docs_dir.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        yield p
        seen += 1
        if max_docs and seen >= max_docs:
            return


def run_shadow(docs_dir: Path, *, exts=_DEFAULT_EXTS, max_docs: int = 0) -> dict:
    pipe, svc, ClassifyRequest = _build_serving()
    stats: list[dict] = []  # 오직 수치 통계만 — 텍스트/파일명 미저장.
    for i, path in enumerate(_iter_docs(docs_dir, exts, max_docs)):
        parse_ok = False
        text = ""
        try:
            pre = pipe.run_file(path)
            text = pre.text
            ex = pre.extraction
            parse_ok = (not ex.error) and ex.quality > 0 and len(text.strip()) > 0
        except Exception:  # noqa: BLE001
            parse_ok = False
        grade = status = None
        conf = 0.0
        rule_fb = False
        t0 = time.perf_counter()
        if parse_ok:
            try:
                # doc_id 는 익명 시퀀스(파일명·경로 미사용 — 반출물에 식별자 유입 차단).
                resp = svc.classify(ClassifyRequest(doc_id=f"shadow-{i}", content=text, return_evidence=False))
                grade = resp.label.value if hasattr(resp.label, "value") else str(resp.label)
                status = resp.status
                conf = float(getattr(resp, "confidence", 0.0) or 0.0)
                rule_fb = any("rule-based fallback" in str(w) for w in (resp.warnings or []))
            except Exception:  # noqa: BLE001
                grade, status = None, "classify_error"
        else:
            status = "parse_fail"
        latency_ms = (time.perf_counter() - t0) * 1000.0 if parse_ok else None
        # text 는 여기서 폐기 — stats 에 넣지 않는다(무반출 계약).
        stats.append({
            "grade": grade, "status": status, "confidence": conf,
            "parse_ok": parse_ok, "latency_ms": latency_ms, "rule_fallback": rule_fb,
        })
        del text
    return aggregate_shadow(stats)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs-dir", required=True, help="실문서 디렉토리(폐쇄망 내부 — rglob)")
    ap.add_argument("--max-docs", type=int, default=0, help="처리 상한(0=전체)")
    ap.add_argument("--out", default="reports/shadow_aggregate.json")
    args = ap.parse_args()

    docs_dir = Path(args.docs_dir)
    if not docs_dir.is_dir():
        print(f"[shadow] FAIL: 디렉토리 없음: {docs_dir}")
        return 2

    agg = run_shadow(docs_dir, max_docs=args.max_docs)
    out = _POC / args.out if not Path(args.out).is_absolute() else Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[shadow] 집계만 반출 — 원문·파일명·per-doc 미포함")
    print(json.dumps(agg, ensure_ascii=True, indent=2))
    print(f"[shadow] done (n={agg.get('n')}) → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

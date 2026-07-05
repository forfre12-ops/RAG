"""고객 인수(acceptance) 러너 — 샘플팩(datasets/acceptance_pack)을 parse→classify→gate 로 채점.

판정 규율(서빙이 의도적으로 안전방향 과분류 → 정확 등급일치는 틀린 기준):
  VETO(불합격): (1) 파싱 실패/빈 텍스트, (2) 숫자 무손실 위반(기대 숫자 토큰 누락 — 표 셀 유실 등),
    (3) under-분류(고등급을 덜 민감한 등급으로 = 미탐; serving_eval 규율상 분류 크래시도 미탐으로 집계).
  REPORT(비-게이트): 정확 등급일치율·over-분류(안전방향) 수·needs_review 라우팅 수·포맷별 품질·지연.
  → 정확도(합성천장 F1 0.26)로 게이트하면 올바른 '안전 과분류' 시스템을 불합격시키므로 절대 금지.

모드:
  --mode inproc : DB/HTTP 불요. PreprocessPipeline + ClassifyService 로 배포 서빙 경로 그대로(개발/lite 검증).
  --mode http   : 실행 중인 배포 API 에 파일 업로드(POST /documents/analyze) — 고객 폐쇄망 검증.

exit 0=PASS, 1=FAIL(veto 발생), 2=설정/입력 오류.  ASCII 로그(cp949 콘솔 안전).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:  # cp949 콘솔 안전 + pytest import 안전
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass
_POC = Path(__file__).resolve().parents[1]

SEVERITY = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}  # 작을수록 더 민감(고등급)


def _numeric_miss(text: str, tokens: list[str]) -> list[str]:
    return [t for t in (tokens or []) if t not in (text or "")]


def judge_doc(expected_grade: str, expected_numbers: list[str], parse_ok: bool,
              text: str, pred_label: str | None, status: str) -> dict:
    """순수 판정 함수(모델 불요 — 단위 테스트 대상). veto 3종 + 보고 항목."""
    miss = _numeric_miss(text, expected_numbers)
    exp_sev = SEVERITY.get(expected_grade, 3)
    pred_sev = SEVERITY.get(pred_label, 3)  # 미상/None → 최저심각도(=미탐 방향으로 보수 집계)
    under = pred_sev > exp_sev
    over = pred_sev < exp_sev
    numeric_ok = not miss
    reasons: list[str] = []
    if not parse_ok:
        reasons.append("parse_fail")
    if not numeric_ok:
        reasons.append("numeric_loss")
    if under:
        reasons.append("under_classified")
    return {
        "expected": expected_grade, "pred": pred_label, "status": status,
        "parse_ok": parse_ok, "numeric_recall": round(1.0 - len(miss) / max(1, len(expected_numbers)), 3),
        "missing_numbers": miss, "under_classified": under, "over_classified": over,
        "exact": pred_label == expected_grade, "veto": bool(reasons), "veto_reasons": reasons,
    }


def run_inproc(pack_dir: Path, docs: list[dict]) -> list[dict]:
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    os.environ.setdefault("TESTING", "1")
    if str(_POC / "src") not in sys.path:
        sys.path.insert(0, str(_POC / "src"))
    from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline  # noqa: PLC0415
    from lloydk.schemas.classify import ClassifyRequest  # noqa: PLC0415
    from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415

    pipe, svc = PreprocessPipeline(), ClassifyService()
    results: list[dict] = []
    for d in docs:
        path = pack_dir / d["file"]
        t0 = time.perf_counter()
        method, tblcov, warnings = "?", None, []
        try:
            pre = pipe.run_file(path)
            text = pre.text
            ex = pre.extraction
            parse_ok = (not ex.error) and ex.quality > 0 and len(text.strip()) > 0
            method, tblcov = ex.method, getattr(ex, "table_coverage", None)
        except Exception as e:  # noqa: BLE001
            j = judge_doc(d["expected_grade"], d["expected_numeric_tokens"], False, "", None, "parse_error")
            results.append({**d, **j, "method": "PARSE_FAIL", "table_coverage": None,
                            "warnings": [f"{type(e).__name__}: {e}"], "elapsed_ms": None})
            continue
        try:
            resp = svc.classify(ClassifyRequest(doc_id=d["doc_id"], content=text, return_evidence=False))
            pred = resp.label.value if hasattr(resp.label, "value") else str(resp.label)
            status, warnings = resp.status, list(resp.warnings)
            elapsed = getattr(resp, "elapsed_ms", None)
        except Exception as e:  # noqa: BLE001
            # 분류 크래시 = 운영서도 분류 불능 → 최저심각도 미탐으로 집계(serving_eval 규율).
            pred, status, warnings, elapsed = None, "classify_error", [f"{type(e).__name__}: {e}"], None
        j = judge_doc(d["expected_grade"], d["expected_numeric_tokens"], parse_ok, text, pred, status)
        results.append({**d, **j, "method": method, "table_coverage": tblcov, "warnings": warnings,
                        "elapsed_ms": elapsed if elapsed is not None else round((time.perf_counter() - t0) * 1000)})
    return results


def run_http(base_url: str, api_key: str, pack_dir: Path, docs: list[dict]) -> list[dict]:
    import httpx  # noqa: PLC0415

    results: list[dict] = []
    with httpx.Client(timeout=120.0) as cli:
        cli.get(f"{base_url}/api/v1/healthz").raise_for_status()
        for d in docs:
            path = pack_dir / d["file"]
            t0 = time.perf_counter()
            try:
                with path.open("rb") as fh:
                    r = cli.post(f"{base_url}/api/v1/documents/analyze",
                                 headers={"X-API-Key": api_key},
                                 files={"file": (path.name, fh)}, data={"return_evidence": "false"})
                elapsed = round((time.perf_counter() - t0) * 1000)
                if r.status_code != 200:
                    # 고등급 문서의 비-200 = 운영 미탐과 동치(serving_eval 규율): pred=None → under.
                    j = judge_doc(d["expected_grade"], d["expected_numeric_tokens"], False,
                                  "", None, f"http_{r.status_code}")
                    results.append({**d, **j, "method": "HTTP_FAIL", "table_coverage": None,
                                    "warnings": [r.text[:120]], "elapsed_ms": elapsed})
                    continue
                body = r.json()
                parse = body.get("parse", {})
                cls = body.get("classification", {})
                # 서버가 전체 텍스트를 안 돌려주므로(text_preview 만) 숫자검증은 미리보기 best-effort +
                # 파싱성공/표피복 신호로 보완. 전수 숫자검증은 inproc 모드가 담당.
                text = body.get("text_preview", "") or ""
                parse_ok = (not parse.get("extract_error")) and float(parse.get("extraction_quality") or 0) > 0
                pred = cls.get("label")
                j = judge_doc(d["expected_grade"], d["expected_numeric_tokens"], parse_ok, text, pred, cls.get("status", "?"))
                results.append({**d, **j, "method": parse.get("extraction_method", "?"),
                                "table_coverage": parse.get("table_coverage"),
                                "warnings": list(parse.get("warnings") or []) + list(cls.get("warnings") or []),
                                "elapsed_ms": cls.get("elapsed_ms") or elapsed})
            except Exception as e:  # noqa: BLE001
                j = judge_doc(d["expected_grade"], d["expected_numeric_tokens"], False, "", None, "http_error")
                results.append({**d, **j, "method": "HTTP_ERR", "table_coverage": None,
                                "warnings": [f"{type(e).__name__}: {e}"], "elapsed_ms": None})
    return results


def _report(results: list[dict], mode: str, max_latency_ms: int) -> tuple[str, dict]:
    from collections import Counter
    n = len(results)
    vetoed = [r for r in results if r["veto"]]
    under = [r for r in results if r["under_classified"]]
    numloss = [r for r in results if r["missing_numbers"]]
    parsefail = [r for r in results if not r["parse_ok"]]
    over = [r for r in results if r["over_classified"]]
    exact = sum(1 for r in results if r["exact"])
    review = [r for r in results if r["status"] == "needs_review"]
    slow = [r for r in results if (r.get("elapsed_ms") or 0) > max_latency_ms]
    verdict = "PASS" if not vetoed else "FAIL"
    summary = {
        "verdict": verdict, "mode": mode, "n": n,
        "veto_count": len(vetoed),
        "under_classified": len(under), "numeric_loss": len(numloss), "parse_fail": len(parsefail),
        "exact_match": exact, "over_classified_safe": len(over), "needs_review": len(review),
        "over_latency_budget": len(slow), "latency_budget_ms": max_latency_ms,
        "by_format": dict(Counter(r["format"] for r in results)),
    }
    lines = [
        "# Acceptance Pack Result",
        "",
        f"- Verdict: **{verdict}**  (mode={mode}, {n} docs)",
        f"- VETO: parse_fail={len(parsefail)} · numeric_loss={len(numloss)} · under_classified(FNR)={len(under)}",
        f"- REPORT (non-gating): exact-match={exact}/{n} · over-classified(safe)={len(over)} · "
        f"needs_review={len(review)} · over-latency(>{max_latency_ms}ms)={len(slow)}",
        "",
        "| doc | fmt | exp | pred | severity | numeric | parse | status | veto |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        sev = "UNDER!" if r["under_classified"] else ("over" if r["over_classified"] else "floor-ok")
        lines.append(
            f"| {r['doc_id']} | {r['format']} | {r['expected']} | {r.get('pred')} | {sev} | "
            f"{r['numeric_recall']} | {'ok' if r['parse_ok'] else 'FAIL'} | {r['status']} | "
            f"{','.join(r['veto_reasons']) or '-'} |"
        )
    if vetoed:
        lines += ["", "## VETO 상세"]
        for r in vetoed:
            lines.append(f"- `{r['doc_id']}` ({r['format']}) exp={r['expected']} pred={r.get('pred')} "
                         f"reasons={r['veto_reasons']} missing_numbers={r['missing_numbers']}")
    lines += [
        "", "## 판정 규율",
        "- VETO(불합격): 파싱실패 / 숫자무손실 위반 / under-분류(고등급 미탐).",
        "- 정확 등급일치·over-분류는 **보고만**(서빙은 의도적 안전 과분류 — 정확도로 게이트하지 않음).",
    ]
    return "\n".join(lines) + "\n", summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="datasets/acceptance_pack", help="샘플팩 디렉토리(expected_labels.json 포함)")
    ap.add_argument("--mode", choices=["inproc", "http"], default="inproc")
    ap.add_argument("--base-url", default="http://localhost:8000", help="http 모드 API base URL")
    ap.add_argument("--api-key", default=os.environ.get("API_KEY", "replace_me_api_key"))
    ap.add_argument("--report", default="reports/acceptance_report.md")
    ap.add_argument("--max-latency-ms", type=int, default=3000, help="문서당 지연 예산(보고용 — veto 아님)")
    args = ap.parse_args()

    pack_dir = _POC / args.pack if not Path(args.pack).is_absolute() else Path(args.pack)
    manifest_path = pack_dir / "expected_labels.json"
    if not manifest_path.exists():
        print(f"[acceptance] FAIL: 팩 매니페스트 없음: {manifest_path}. 먼저 `make acceptance-pack`.")
        return 2
    docs = json.loads(manifest_path.read_text(encoding="utf-8")).get("docs", [])
    if not docs:
        print("[acceptance] FAIL: 팩에 문서 0건.")
        return 2

    if args.mode == "http":
        results = run_http(args.base_url, args.api_key, pack_dir, docs)
    else:
        results = run_inproc(pack_dir, docs)

    md, summary = _report(results, args.mode, args.max_latency_ms)
    out = _POC / args.report if not Path(args.report).is_absolute() else Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    print(f"[acceptance] {summary['verdict']}  (report: {out})")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

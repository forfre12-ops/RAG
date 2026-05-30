"""P5 PoC — E2E 스모크 테스트.

두 모드:
  --mode http   : 외부 FastAPI 서버에 HTTP POST (인프라 기동 필요)
  --mode inproc : FastAPI TestClient로 in-process 호출 (Docker 없이 검증 가능)

합격선: 응답 status 200 + label ∈ {TS,S1,S2,S3} + 소요시간 ≤ 30s
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


SAMPLES = [
    {
        "doc_id": "smoke-ts-001",
        "tenant_id": "poc",
        "title": "차세대 제품 설계도",
        "content": (
            "본 문서는 특급기밀 등급의 차세대 제품 설계도와 핵심 원천기술 명세를 담는다. "
            "M&A 계획과 회사 매각 협상 진행 중이며 임직원 인사 이동 사항을 포함한다."
        ),
        "expected_grade": "TS",
    },
    {
        "doc_id": "smoke-s1-001",
        "tenant_id": "poc",
        "title": "신제품 개발 로드맵",
        "content": (
            "1급 비밀로 분류된 영업비밀 자료. 공정 노하우와 원가 구조, 고객 데이터베이스 분석. "
            "신제품 개발 로드맵과 알고리즘 소스코드, 마케팅 전략 포함."
        ),
        "expected_grade": "S1",
    },
    {
        "doc_id": "smoke-s2-001",
        "tenant_id": "poc",
        "title": "분기 사업 검토",
        "content": (
            "대외비 2급 자료. 내부 검토용 분기 매출 보고서, 사업 계획 초안, "
            "거래처 명단, 예산 배정 검토 자료. 조직 개편 검토."
        ),
        "expected_grade": "S2",
    },
    {
        "doc_id": "smoke-s3-001",
        "tenant_id": "poc",
        "title": "신제품 출시 보도자료",
        "content": (
            "공시 및 공고용 보도자료. 회사 소개와 채용 공고, 이용약관 외부 공지. "
            "FAQ 및 사보 콘텐츠 모음. 공개 가능 자료."
        ),
        "expected_grade": "S3",
    },
]


def via_http(url: str, api_key: str) -> list[dict]:
    import httpx

    out: list[dict] = []
    with httpx.Client(timeout=60.0) as cli:
        r = cli.get(f"{url}/api/v1/healthz")
        r.raise_for_status()
        for s in SAMPLES:
            payload = {**s}
            payload.pop("expected_grade", None)
            payload.update({"use_rag": True, "return_evidence": True})
            t0 = time.perf_counter()
            r = cli.post(
                f"{url}/api/v1/classify",
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                json=payload,
            )
            elapsed = (time.perf_counter() - t0) * 1000
            r.raise_for_status()
            data = r.json()
            data["expected_grade"] = s["expected_grade"]
            data["elapsed_ms_client"] = round(elapsed, 1)
            out.append(data)
    return out


def via_inproc() -> list[dict]:
    from fastapi.testclient import TestClient

    from lloydk.api.app import app
    from lloydk.config import settings

    out: list[dict] = []
    with TestClient(app) as cli:
        r = cli.get("/api/v1/healthz")
        assert r.status_code == 200
        for s in SAMPLES:
            payload = {**s}
            payload.pop("expected_grade", None)
            payload.update({"use_rag": True, "return_evidence": True})
            t0 = time.perf_counter()
            r = cli.post(
                "/api/v1/classify",
                headers={"X-API-Key": settings.api_key},
                json=payload,
            )
            elapsed = (time.perf_counter() - t0) * 1000
            assert r.status_code == 200, r.text
            data = r.json()
            data["expected_grade"] = s["expected_grade"]
            data["elapsed_ms_client"] = round(elapsed, 1)
            out.append(data)
    return out


def write_report(results: list[dict], mode: str, out: Path) -> str:
    n = len(results)
    ok_status = all(r.get("label") in {"TS", "S1", "S2", "S3"} for r in results)
    grade_match = sum(1 for r in results if r.get("label") == r.get("expected_grade"))
    max_elapsed = max((r["elapsed_ms_client"] for r in results), default=0)
    time_pass = max_elapsed <= 30_000  # 30s
    verdict = "PASS" if (ok_status and time_pass) else "FAIL"

    md = [
        f"# P5 — E2E 스모크 리포트 ({mode})",
        "",
        f"- **판정**: {verdict}",
        f"- 응답 OK (label ∈ TS/S1/S2/S3): {'PASS' if ok_status else 'FAIL'} ({n}건)",
        f"- 라벨 일치도: {grade_match}/{n} ({grade_match/n*100:.0f}%)",
        f"- 최대 소요시간 ≤ 30s: {max_elapsed:.0f}ms ({'PASS' if time_pass else 'FAIL'})",
        "",
        "| doc_id | expected | predicted | conf | elapsed | model |",
        "|---|:---:|:---:|---:|---:|---|",
    ]
    for r in results:
        md.append(
            f"| {r.get('doc_id')} | {r.get('expected_grade')} | {r.get('label')} | "
            f"{r.get('confidence', 0):.3f} | {r.get('elapsed_ms_client')}ms | {r.get('model_version')} |"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["http", "inproc"], default="inproc")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--api-key", default="lloydk_dev_apikey")
    ap.add_argument("--report", default="reports/p5_e2e_report.md")
    args = ap.parse_args()

    try:
        if args.mode == "http":
            results = via_http(args.url, args.api_key)
        else:
            results = via_inproc()
    except Exception as exc:  # noqa: BLE001
        print(f"[p5] FAIL: {exc}", file=sys.stderr)
        return 1

    verdict = write_report(results, args.mode, Path(args.report))
    for r in results:
        print(f"  {r['doc_id']}: expect={r['expected_grade']}, pred={r['label']}, "
              f"conf={r['confidence']:.3f}, {r['elapsed_ms_client']}ms")
    print(f"\n[p5] verdict: {verdict} -> {args.report}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

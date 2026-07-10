"""W2 소크(soak) 드라이버 — 지속 부하 하 안정성·자원 성장·안전 veto 유지 검증.

파일럿 30일 무인 운영의 축약 프록시: 코퍼스(기본 acceptance_pack)를 하나의 **영속** 서빙
싱글턴에 반복 투입하며 주기(cycle)마다 스냅샷을 남긴다 — 메모리(RSS) 성장, escalation(검수
라우팅)율, 고등급(TS/S1) 미탐(veto miss), 오류율, 지연. run_acceptance 의 parse→classify→
judge 경로를 그대로 재사용(정합), 단 서비스는 사이클 간 재생성하지 않아 누수·드리프트가 드러난다.

판정(안전 우선):
  FAIL 조건 — (1) 고등급 veto miss > 0(미탐 = 안전 붕괴), (2) RSS 성장 > 예산(누수),
              (3) 오류율 > 예산.  그 외 PASS. escalation율·지연은 보고(추세).

모드: --mode inproc(기본, DB/HTTP 불요) — 배포 서빙 경로 그대로. exit 0=PASS,1=FAIL,2=입력오류.
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
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_HIGH = ("TS", "S1")


def _rss_mb() -> float | None:
    try:
        import os as _os

        import psutil  # noqa: PLC0415
        return psutil.Process(_os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:  # noqa: BLE001
        return None


def soak_verdict(
    rss_first: float | None,
    rss_last: float | None,
    high_grade_miss_total: int,
    error_rate: float,
    *,
    max_rss_growth_mb: float,
    max_error_rate: float,
) -> tuple[str, list[str], float]:
    """순수 판정(단위 테스트 대상). 반환: (verdict, reasons, rss_growth_mb)."""
    growth = (
        (rss_last - rss_first)
        if (rss_first is not None and rss_last is not None)
        else 0.0
    )
    reasons: list[str] = []
    if high_grade_miss_total > 0:
        reasons.append(f"high_grade_veto_miss={high_grade_miss_total}")
    if growth > max_rss_growth_mb:
        reasons.append(f"rss_growth={growth:.1f}MB>{max_rss_growth_mb}MB")
    if error_rate > max_error_rate:
        reasons.append(f"error_rate={error_rate:.4f}>{max_error_rate}")
    return ("PASS" if not reasons else "FAIL", reasons, growth)


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


def _preparse(pipe, pack_dir: Path, docs: list[dict]) -> list[tuple[dict, str, bool]]:
    """코퍼스를 1회만 파싱해 (doc, text, parse_ok) 로 캐시 — 사이클은 서빙 경로 소크에 집중."""
    corpus: list[tuple[dict, str, bool]] = []
    for d in docs:
        try:
            pre = pipe.run_file(pack_dir / d["file"])
            text = pre.text
            ex = pre.extraction
            parse_ok = (not ex.error) and ex.quality > 0 and len(text.strip()) > 0
        except Exception:  # noqa: BLE001
            text, parse_ok = "", False
        corpus.append((d, text, parse_ok))
    return corpus


def _run_cycle(svc, ClassifyRequest, corpus: list[tuple[dict, str, bool]]) -> dict:
    from run_acceptance import judge_doc  # noqa: PLC0415

    n = under_high = needs_review = errors = 0
    lat_sum = 0.0
    for d, text, parse_ok in corpus:
        n += 1
        t0 = time.perf_counter()
        try:
            resp = svc.classify(
                ClassifyRequest(doc_id=d["doc_id"], content=text, return_evidence=False)
            )
            pred = resp.label.value if hasattr(resp.label, "value") else str(resp.label)
            status = resp.status
        except Exception:  # noqa: BLE001
            pred, status, errors = None, "classify_error", errors + 1
        lat_sum += (time.perf_counter() - t0) * 1000.0
        j = judge_doc(d["expected_grade"], d.get("expected_numeric_tokens", []), parse_ok, text, pred, status)
        if status == "needs_review":
            needs_review += 1
        if j["under_classified"] and d["expected_grade"] in _HIGH:
            under_high += 1
    return {
        "n": n,
        "under_high": under_high,
        "needs_review": needs_review,
        "errors": errors,
        "lat_mean_ms": round(lat_sum / n, 1) if n else 0.0,
        "escalation_rate": round(needs_review / n, 4) if n else 0.0,
    }


def run_soak(pack_dir: Path, docs: list[dict], *, cycles: int, snapshot_every: int) -> dict:
    pipe, svc, ClassifyRequest = _build_serving()
    corpus = _preparse(pipe, pack_dir, docs)
    snapshots: list[dict] = []
    high_grade_miss_total = 0
    err_total = req_total = 0
    rss_first = _rss_mb()
    for c in range(1, cycles + 1):
        cyc = _run_cycle(svc, ClassifyRequest, corpus)
        high_grade_miss_total += cyc["under_high"]
        err_total += cyc["errors"]
        req_total += cyc["n"]
        if c == 1 or c == cycles or (c % max(snapshot_every, 1) == 0):
            snap = {"cycle": c, "rss_mb": round(_rss_mb() or 0.0, 1), **cyc}
            snapshots.append(snap)
            print(f"  [soak] cycle {c}/{cycles} rss={snap['rss_mb']}MB "
                  f"esc={cyc['escalation_rate']} high_miss={cyc['under_high']} "
                  f"err={cyc['errors']} lat={cyc['lat_mean_ms']}ms")
    rss_last = snapshots[-1]["rss_mb"] if snapshots else rss_first
    # 누수 baseline = 첫 스냅샷(cycle 1, warmup 후). rss_first(warmup 전)에서 재면 1회성
    # 콜드스타트 모델·임베더 로드(+수백MB)가 '누수'로 오판된다 → 정상상태(cycle1→N) 성장만 본다.
    rss_baseline = snapshots[0]["rss_mb"] if snapshots else (rss_first or 0.0)
    error_rate = (err_total / req_total) if req_total else 0.0
    return {
        "cycles": cycles,
        "docs_per_cycle": len(corpus),
        "requests_total": req_total,
        "high_grade_veto_miss_total": high_grade_miss_total,
        "error_rate": round(error_rate, 5),
        "rss_first_mb": round(rss_first or 0.0, 1),        # warmup 전(참고: 콜드스타트 델타 산출용)
        "rss_baseline_mb": round(rss_baseline or 0.0, 1),  # warmup 후 = 누수 판정 baseline
        "rss_last_mb": round(rss_last or 0.0, 1),
        "snapshots": snapshots,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", default="datasets/acceptance_pack")
    ap.add_argument("--cycles", type=int, default=50, help="반복 투입 사이클 수(30일 축약 프록시)")
    ap.add_argument("--snapshot-every", type=int, default=10)
    ap.add_argument("--max-docs", type=int, default=0, help="코퍼스 상한(0=전체) — 빠른 스모크용")
    ap.add_argument("--max-rss-growth-mb", type=float, default=150.0, help="RSS 성장 예산(누수 판정)")
    ap.add_argument("--max-error-rate", type=float, default=0.01)
    ap.add_argument("--report", default="reports/soak_report.json")
    args = ap.parse_args()

    pack_dir = _POC / args.pack if not Path(args.pack).is_absolute() else Path(args.pack)
    manifest = pack_dir / "expected_labels.json"
    if not manifest.exists():
        print(f"[soak] FAIL: 팩 매니페스트 없음: {manifest}. 먼저 `make acceptance-pack`.")
        return 2
    docs = json.loads(manifest.read_text(encoding="utf-8")).get("docs", [])
    if args.max_docs > 0:
        docs = docs[: args.max_docs]
    if not docs:
        print("[soak] FAIL: 코퍼스 0건.")
        return 2

    result = run_soak(pack_dir, docs, cycles=args.cycles, snapshot_every=args.snapshot_every)
    verdict, reasons, growth = soak_verdict(
        result["rss_baseline_mb"], result["rss_last_mb"],  # warmup 후 baseline → 정상상태 누수만
        result["high_grade_veto_miss_total"], result["error_rate"],
        max_rss_growth_mb=args.max_rss_growth_mb, max_error_rate=args.max_error_rate,
    )
    result["verdict"] = verdict
    result["fail_reasons"] = reasons
    result["rss_growth_mb"] = round(growth, 1)

    out = _POC / args.report if not Path(args.report).is_absolute() else Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[soak] {verdict}  cycles={result['cycles']} rss_growth={result['rss_growth_mb']}MB "
          f"high_miss={result['high_grade_veto_miss_total']} err_rate={result['error_rate']} "
          f"reasons={reasons or '-'}  (report: {out})")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""저신뢰 문서를 **비밀 확률질량**으로 다시 가를 수 있는지 검증한다.

왜. 현재 게이트는 `max(scores) < 0.70` 이면 검수로 보낸다. 그런데 사내 일상문서 500건을
태워 보니 검수로 간 161건 중 **160건(99.4%)이 S2/S3**, 즉 영업비밀이 아닌데 사람이 본다.
확신이 낮은 이유가 "S2 냐 S3 냐" 였고 "비밀일 수 있다" 가 아니었다.

    지금        max(scores) < 0.70            -> 검수     등급과 무관
    문제        S2/S3 혼동과 TS/S1 가능성을 같은 잣대로 잰다

미탐은 **낮게 본 오류**다. S3 를 S2 로 봐도 그 반대여도 유출 위험은 0 이다. 그러니
"비밀인가" 의 불확실성만 검수 사유가 되어야 한다. 그 양은 `P(TS)+P(S1)` 이다.

    제안        max < 0.70 이어도 P(TS)+P(S1) < theta 면 자동확정

⚠ 사내 일상문서 500건은 **전부 정답 S3** 라 편향돼 있다. 거기서 안전해 보이는 theta 가
   혼합 코퍼스에서도 안전한지는 별개다. 이 스크립트는 라벨이 섞인 콘솔 후보 306건에서
   그 문턱을 검증한다 — 그 셋에는 TS/S1 이 141건 있다.

⚠ 라벨이 기계 라벨이다. 결론은 "이 문턱이 그 라벨 기준으로 새는가" 이지 절대 안전이 아니다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

SECRET = ("TS", "S1")


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (c + r) / d)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="비밀 확률질량 게이트 검증")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", default="reports/SECRET_MASS_GATE.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")

    from koipa.schemas.classify import ClassifyRequest
    from koipa.services.classify_service import ClassifyService
    from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService

    gsvc = ProxyGoldCandidateService()
    cands = gsvc.list_candidates()["candidates"]
    if args.limit:
        cands = cands[:args.limit]
    print(f"[data] 콘솔 후보 {len(cands)}건 (라벨 혼합)\n")

    svc = ClassifyService()
    out = []
    t0 = time.perf_counter()
    for i, c in enumerate(cands):
        detail = gsvc.get_candidate(c["doc_id"])
        text = (detail or {}).get("text") or ""
        lab = c.get("proposed_grade") or c.get("final_grade")
        if not text or not lab:
            continue
        try:
            resp = svc.classify(ClassifyRequest(doc_id=c["doc_id"], content=text,
                                                return_evidence=False))
        except Exception as exc:  # noqa: BLE001
            print(f"  [skip] {c['doc_id']}: {type(exc).__name__}")
            continue
        sc = {k: float(v) for k, v in (getattr(resp, "scores", None) or {}).items()}
        out.append({
            "doc_id": c["doc_id"], "label": lab,
            "v5": resp.label.value if hasattr(resp.label, "value") else str(resp.label),
            "status": resp.status,
            "conf": round(float(resp.confidence or 0.0), 4),
            "secret_mass": round(sc.get("TS", 0.0) + sc.get("S1", 0.0), 4),
            "scores": {k: round(v, 4) for k, v in sc.items()},
        })
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(cands)} · {time.perf_counter() - t0:.0f}s")

    n = len(out)
    print(f"\n[done] {n}건 · {time.perf_counter() - t0:.0f}s\n")
    lo = [r for r in out if r["status"] == "needs_review"]
    lo_secret = [r for r in lo if r["label"] in SECRET]
    print(f"검수로 간 것 {len(lo)}건 · 그중 정답이 영업비밀인 것 {len(lo_secret)} "
          f"= {len(lo_secret) / max(1, len(lo)):.1%}")
    print("   -> 이 비율이 높으면 현재 게이트가 제 일을 하고 있다는 뜻이다\n")

    print("비밀 확률질량 문턱을 걸어 저신뢰분을 되살리면")
    print(f"{'theta':>7s}{'되살림':>8s}{'비율':>8s}{'그중 실제비밀':>13s}{'누락률':>9s}{'95%상한':>10s}")
    rows = []
    for th in (0.05, 0.10, 0.15, 0.20, 0.30):
        rescued = [r for r in lo if r["secret_mass"] < th]
        leaked = [r for r in rescued if r["label"] in SECRET]
        up = wilson_upper(len(leaked), len(rescued))
        rows.append({"theta": th, "rescued": len(rescued), "leaked": len(leaked),
                     "leak_rate": round(len(leaked) / max(1, len(rescued)), 4),
                     "upper95": round(up, 5)})
        print(f"{th:>7.2f}{len(rescued):>8d}{len(rescued) / max(1, len(lo)):>8.1%}"
              f"{len(leaked):>13d}{len(leaked) / max(1, len(rescued)):>9.2%}{up:>10.4f}")

    # 참고 — 지금 자동확정되는 것의 누락률(비교 기준선)
    auto = [r for r in out if r["status"] != "needs_review"]
    auto_leak = [r for r in auto if r["label"] in SECRET and r["v5"] not in SECRET]
    print(f"\n기준선 — 현재 자동확정 {len(auto)}건의 비밀 누락 {len(auto_leak)} "
          f"= {len(auto_leak) / max(1, len(auto)):.2%} · 95% 상한 "
          f"{wilson_upper(len(auto_leak), len(auto)):.4f}")
    print("   -> 되살린 분의 누락률이 이 기준선보다 나쁘면 그 문턱은 쓰면 안 된다")

    Path(args.report).write_text(json.dumps({"n": n, "needs_review": len(lo),
                                             "thresholds": rows, "rows": out},
                                            ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ 기계 라벨 기준이다. 절대 안전이 아니라 상대 비교다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

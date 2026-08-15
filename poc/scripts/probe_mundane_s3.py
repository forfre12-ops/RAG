"""내부 일상문서("의미 없는 문서")를 지금 배포본이 어떻게 처리하는지 잰다.

왜. S3 자동확정은 지금 **출처가 공개일 때만** 걸린다(Gate-1). 그런데 회원사 문서는
대부분 내부 문서라 그 경로가 안 열린다 — 자동화 이득이 거기서 끊긴다.

그런데 정본은 곱셈이라 **어느 한 요소가 0 으로 입증되면** 나머지를 몰라도 S3 다.
그리고 가치 부재는 비공지성과 달리 **본문에서 읽힌다** — 헌혈 행사 안내는 실제로
독립적 경제가치가 없고 그것이 글에 보인다.

    경로 1  출처가 공개 -> secrecy = 0 -> S3     구현됨. 내부 문서엔 안 걸림
    경로 2  보호할 내용 없음 -> value = 0 -> S3   **미구현**

이 스크립트는 경로 2 가 실현 가능한지를 먼저 실측한다. 두 가지를 본다:

    1. v5 운영 경로가 이 문서들을 S3 로 보는가, 그리고 자동확정하는가
    2. v8 요소 경로의 value 헤드가 proven_absent 를 내는가

⚠ 이 셋은 합성이다(사내 안내문 생성). 실제 회원사 일상문서와 표면이 다를 수 있다.
   여기서 잘 된다고 실문서에서 된다는 뜻이 아니다 — 가능성의 하한을 보는 것이다.
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
    ap = argparse.ArgumentParser(description="내부 일상문서 처리 실측")
    ap.add_argument("--data", default="datasets/mundane_s3/holdout.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", default="reports/MUNDANE_PROBE.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")

    from koipa.config import settings
    from koipa.schemas.classify import ClassifyRequest
    from koipa.services.classify_service import ClassifyService

    rows = [json.loads(l) for l in Path(args.data).read_text("utf-8").splitlines() if l.strip()]
    if args.limit:
        rows = rows[:args.limit]
    print(f"[data] {args.data} — {len(rows)}건 (전부 정답 S3)\n")

    use_factor = bool(settings.factor_model_dir)
    inf = None
    if use_factor:
        from koipa.modules.m5_inference.factor_model import get_factor_inference

        inf = get_factor_inference(settings.factor_model_dir, base=settings.factor_model_base,
                                   max_len=settings.factor_model_max_len)
        if not inf.load():
            print(f"[warn] 요소 모델 미로드: {inf.load_error}")
            inf = None

    svc = ClassifyService()
    out = []
    t0 = time.perf_counter()
    for i, r in enumerate(rows):
        text = r.get("text") or ""
        try:
            resp = svc.classify(ClassifyRequest(doc_id=f"mundane-{i:04d}", content=text,
                                                return_evidence=False))
            v5 = resp.label.value if hasattr(resp.label, "value") else str(resp.label)
            status, warns = resp.status, list(resp.warnings)
        except Exception as exc:  # noqa: BLE001
            print(f"  [skip] {i}: {type(exc).__name__}")
            continue
        rec = {"i": i, "v5": v5, "status": status,
               "conf": round(float(getattr(resp, "confidence", 0.0) or 0.0), 4),
               "chars": len(text),
               "scores": {k: round(float(v), 4) for k, v in
                          (getattr(resp, "scores", None) or {}).items()},
               "warns": [w[:70] for w in warns if "persistence" not in w]}
        if inf is not None:
            p = inf.predict(text)
            if p:
                codes, probs = p
                names = {"secrecy": codes[0], "value": codes[1], "management": codes[2]}
                nm = {0: "proven_absent", 1: "lv1", 2: "lv2", 3: "unknown"}
                rec["v8_factors"] = {k: nm[v] for k, v in names.items()}
                rec["v8_value_absent_p"] = round(probs[1][0], 4)
        out.append(rec)
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(rows)} · {time.perf_counter() - t0:.0f}s")

    n = len(out)
    print(f"\n[done] {n}건 · {time.perf_counter() - t0:.0f}s\n")
    print("1) v5 운영 경로")
    dist = Counter(r["v5"] for r in out)
    print(f"   등급 분포 {dict(dist)}")
    s3 = [r for r in out if r["v5"] == "S3"]
    print(f"   S3 정확도 {len(s3)}/{n} = {len(s3) / n:.1%}")
    auto = [r for r in out if r["status"] != "needs_review"]
    auto_s3 = [r for r in auto if r["v5"] == "S3"]
    print(f"   자동확정 {len(auto)}/{n} = {len(auto) / n:.1%} · 그중 S3 {len(auto_s3)}")
    over = [r for r in out if r["v5"] in ("TS", "S1")]
    print(f"   고등급 과분류 {len(over)}건 — 검수 부담")
    st = Counter(r["status"] for r in out)
    print(f"   상태 {dict(st)}")

    if inf is not None:
        print("\n2) v8 요소 경로 — value 가 부재를 입증하는가")
        vf = Counter(r.get("v8_factors", {}).get("value") for r in out if "v8_factors" in r)
        print(f"   value {dict(vf)}")
        sf = Counter(r.get("v8_factors", {}).get("secrecy") for r in out if "v8_factors" in r)
        print(f"   secrecy {dict(sf)}")
        strong = [r for r in out if r.get("v8_value_absent_p", 0) >= 0.99]
        print(f"   value=absent 확신 0.99 이상 {len(strong)}/{n} = {len(strong) / n:.1%}")
        print("   -> 이 비율이 곧 경로 2 로 자동확정 가능한 상한이다")

    Path(args.report).write_text(json.dumps({"n": n, "rows": out}, ensure_ascii=False, indent=2),
                                 "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ 합성 사내 안내문이다. 실 회원사 일상문서와 표면이 다를 수 있다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

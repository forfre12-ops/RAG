"""릴리스 게이트가 재는 것은 **원시 모델**이다 — 우리가 출하하는 것은 서빙 경로다.

왜(실측 2026-08-15). 폐쇄망 번들 빌드가 release-gate 에서 막혔고, 원인을 좁히니
`p1.public` 한 축이었다.

    f1_macro          0.9547  >= 0.75   통과
    fnr_underclass    0.0580  <= 0.05   실패   <- 여기
    high_risk_to_s3   0       == 0      통과

과소분류 내역은 딱 한 종류(S1 -> S2 4건)이고 모수는 TS+S1 = 69 다. 임계 0.05 x 69 =
3.45 이므로 **3건이면 통과, 4건이라 실패**다. 문서 하나 차이다.

그런데 그 숫자는 `eval_p1_model_gold.predict_direct` 로 낸 것이다 - **원시 모델 출력**이고
서빙 게이트(FNR-safe 상향 tau=0.30 · 합의 게이트 · metadata floor · 검수 라우팅)를 전혀
안 탄다. 실제 배포본은 그 게이트를 지나서 등급과 status 를 낸다.

4건을 서빙 경로로 태워 보니 갈렸다.

    fdd5193d  S1 <- 서빙이 등급을 **복구**했다        needs_review
    4543d67c  S2                                    needs_review   (사람이 본다)
    3bb5fe37  S2                                    staging        <- 무음 미탐
    df098393  S2                                    staging        <- 무음 미탐

이 스크립트는 같은 판정면(public tier)을 **두 방식으로 나란히** 재서 그 차이를 수치로
남긴다.

⚠ 이 스크립트는 게이트를 바꾸지 않는다. 판정 기준을 건드리면 "막히니까 기준을 옮겼다"가
  되고 감리에서 이력이 그대로 드러난다. 여기서 하는 일은 **증거를 만드는 것뿐**이고,
  기준을 바꿀지는 사람이 정한다.

⚠ 무음 미탐의 정의를 맞춘다: status != needs_review 인데 등급이 정답보다 낮은 것.
  needs_review 는 사람이 보므로 무음이 아니다(다른 측정 스크립트와 같은 규약).
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

ORDER = ["TS", "S1", "S2", "S3"]
RANK = {g: i for i, g in enumerate(ORDER)}
HIGH = ("TS", "S1")
PUBLIC_TIER = ("koipa_case_based", "nkt_designated", "public_definitive")


def f1_macro(pairs: list[tuple[str, str]]) -> float:
    """등급별 F1 의 단순 평균. **정확도(exact/n)와 다른 값이다.**

    ⚠ 나는 한 번 이 둘을 섞었다(build_operational_readiness 에서 exact/n 을 f1_macro 라
      부름). 불균형이 있으면 정확도가 F1 보다 훨씬 높게 나와 판정이 헐거워진다.
    """
    tot = 0.0
    for g in ORDER:
        tp = sum(1 for t, p in pairs if t == g and p == g)
        fp = sum(1 for t, p in pairs if t != g and p == g)
        fn = sum(1 for t, p in pairs if t == g and p != g)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        tot += (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return round(tot / len(ORDER), 4)


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
    ap = argparse.ArgumentParser(description="원시 모델 vs 서빙 경로 — 같은 판정면")
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_v5_clean/v-fe4b386b")
    ap.add_argument("--report", default="reports/SERVING_VS_RAW.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    os.environ.setdefault("METADATA_FLOOR_ENABLED", "true")
    os.environ["CLASSIFIER_MODEL_DIR"] = args.model_dir

    from koipa.schemas.classify import ClassifyRequest
    from koipa.services.classify_service import ClassifyService

    rows = [
        json.loads(l)
        for l in Path(args.gold).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    tier = [r for r in rows if r.get("label_source") in PUBLIC_TIER]
    print(f"[판정면] public tier {len(tier)}건 "
          f"{dict(Counter(r.get('label_source') for r in tier))}\n")

    svc = ClassifyService()
    out = []
    t0 = time.perf_counter()
    for i, r in enumerate(tier):
        text = r.get("text") or r.get("content") or ""
        truth = r.get("label") or r.get("grade")
        try:
            res = svc.classify(ClassifyRequest(doc_id=r.get("doc_id", f"d{i}"),
                                               content=text, return_evidence=False))
            pred = res.label.value if hasattr(res.label, "value") else str(res.label)
            status = res.status
        except Exception as exc:  # noqa: BLE001
            pred, status = None, f"error:{type(exc).__name__}"
        out.append({"doc_id": r.get("doc_id"), "source": r.get("label_source"),
                    "truth": truth, "serving": pred, "status": status})
        if (i + 1) % 40 == 0:
            print(f"  {i + 1}/{len(tier)} · {time.perf_counter() - t0:.0f}s")

    graded = [o for o in out if o["serving"] in RANK and o["truth"] in RANK]
    high_n = sum(1 for o in graded if o["truth"] in HIGH)
    under = [o for o in graded if RANK[o["serving"]] > RANK[o["truth"]]]
    silent = [o for o in under if o["status"] != "needs_review"]
    to_s3 = [o for o in silent if o["serving"] == "S3" and o["truth"] in HIGH]
    exact = sum(1 for o in graded if o["serving"] == o["truth"])
    auto = [o for o in graded if o["status"] != "needs_review"]

    print(f"\n[서빙 경로] n={len(graded)} · {time.perf_counter() - t0:.0f}s")
    print(f"  등급 일치        {exact}/{len(graded)} = {exact / len(graded):.4f}")
    print(f"  자동확정         {len(auto)}/{len(graded)} = {len(auto) / len(graded):.4f}")
    print(f"  과소분류(전체)    {len(under)}건")
    print(f"  무음 미탐        {len(silent)}건  (needs_review 는 사람이 보므로 제외)")
    print(f"  고등급 모수      {high_n}")
    fnr = len(silent) / high_n if high_n else 0.0
    print(f"  fnr_underclass  {fnr:.4f}   (게이트 임계 0.05)  "
          f"{'통과' if fnr <= 0.05 else '실패'}")
    print(f"  high_risk->S3   {len(to_s3)}      (게이트 임계 0)   "
          f"{'통과' if not to_s3 else '실패'}")
    print(f"  95% 상한        {wilson_upper(len(silent), high_n):.4f}")

    print("\n  과소분류 내역")
    for o in under:
        mark = "무음" if o["status"] != "needs_review" else "검수"
        print(f"    [{mark}] {o['truth']}->{o['serving']}  {o['doc_id']}  {o['source']}")

    payload = {
        "model_dir": args.model_dir, "tier": "public", "n": len(graded),
        "serving": {
            # n 을 여기에도 둔다. 소비자가 payload["n"] 을 못 찾아 0 으로 읽고 f1 을
            # 147 같은 값으로 계산한 사고가 있었다(2026-08-16).
            "n": len(graded),
            "exact": exact,
            "exact_rate": round(exact / len(graded), 4) if graded else 0.0,
            "f1_macro": f1_macro([(o["truth"], o["serving"]) for o in graded]),
            "auto_confirm": len(auto),
            "underclass_all": len(under), "silent_underclass": len(silent),
            "high_n": high_n, "fnr_underclass": round(fnr, 4),
            "high_risk_to_s3": len(to_s3),
            "silent_95_upper": round(wilson_upper(len(silent), high_n), 4),
        },
        "note": "게이트는 바꾸지 않는다. 이 파일은 증거이고 기준 변경은 사람이 정한다.",
        "rows": out,
    }
    Path(args.report).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n원시 모델(release gate 가 재는 것): fnr_underclass 0.0580 · 과소분류 4건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

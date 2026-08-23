"""콘솔 후보 전체에 v5(운영)와 v8(요소) 를 나란히 태워 **불일치 분포**를 잰다.

왜. 인수 팩 10건 섀도에서 agree 6 · factor_higher 4 · factor_lower 0 이 나왔다.
방향은 좋았지만(v8 이 낮게 본 문서 0건) n=10 이라 비율로 읽을 수 없다. 거부 조건
(A2 — v8 이 미탐 방향으로 이견이면 needs_review)을 걸기 전에 **검수량이 얼마나
늘고 무엇을 잡는지**를 알아야 한다.

⚠ 이 셋의 라벨은 기계 라벨이다(codex_review · llm_judge · koipa_case_based).
   그래서 여기서 나오는 것은 **정확도가 아니라 두 모델의 불일치 분포**다. 정확도
   주장에는 사람 검수가 필요하고 그것이 ff5a822c 120건이 기다리는 이유다.

무엇을 답하려는가:

    1. 두 모델이 얼마나 다른가 (agree / factor_higher / factor_lower)
    2. A2 를 걸면 검수가 몇 건 늘어나는가
    3. 그 증가분이 **기계 라벨 기준으로** 미탐을 잡는가, 헛수고인가
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

ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}


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
    ap = argparse.ArgumentParser(description="v5/v8 섀도 전수 대조")
    ap.add_argument("--batch", default=None, help="review_batch 로 좁힌다(예: ff5a822c)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--report", default="reports/V8_SHADOW_SWEEP.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")

    from koipa.config import settings
    from koipa.modules.m5_inference.factor_model import (
        apply_serving_gate,
        get_factor_inference,
        shadow_compare,
    )
    from koipa.schemas.classify import ClassifyRequest
    from koipa.services.classify_service import ClassifyService
    from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService

    if not settings.factor_model_dir:
        print("[error] FACTOR_MODEL_DIR 미지정 — 섀도를 잴 수 없다")
        return 2
    inf = get_factor_inference(settings.factor_model_dir, base=settings.factor_model_base,
                               max_len=settings.factor_model_max_len)
    if not inf.load():
        print(f"[error] 요소 모델 로드 실패: {inf.load_error}")
        return 2

    gsvc = ProxyGoldCandidateService()
    rows = gsvc.list_candidates(review_batch=args.batch)["candidates"] if args.batch \
        else gsvc.list_candidates()["candidates"]
    if args.limit:
        rows = rows[:args.limit]
    print(f"[data] 후보 {len(rows)}건" + (f" (batch={args.batch})" if args.batch else ""))

    svc = ClassifyService()
    out: list[dict] = []
    t0 = time.perf_counter()
    for i, c in enumerate(rows):
        detail = gsvc.get_candidate(c["doc_id"]) if hasattr(gsvc, "get_candidate") else None
        text = (detail or {}).get("text") or c.get("text") or ""
        if not text:
            continue
        try:
            resp = svc.classify(ClassifyRequest(doc_id=c["doc_id"], content=text,
                                                return_evidence=False))
            v5 = resp.label.value if hasattr(resp.label, "value") else str(resp.label)
            status = resp.status
        except Exception as exc:  # noqa: BLE001
            print(f"  [skip] {c['doc_id']}: {type(exc).__name__}")
            continue
        pred = inf.predict(text)
        if pred is None:
            continue
        codes, probs = pred
        fp = apply_serving_gate(codes, probs, metadata=None,
                                tau=settings.factor_tau, kappa=settings.factor_kappa)
        cmp_ = shadow_compare(v5, fp)
        out.append({
            "doc_id": c["doc_id"],
            "label": c.get("proposed_grade") or c.get("final_grade"),
            "origin": c.get("document_origin"),
            "v5": v5, "v8": fp.serving_grade, "direction": cmp_["direction"],
            "v5_status": status, "v8_auto": fp.auto_confirmable,
            "min_conf": cmp_["min_confidence"], "factors": fp.named,
        })
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(rows)} · {time.perf_counter() - t0:.0f}s")

    n = len(out)
    print(f"\n[done] {n}건 · {time.perf_counter() - t0:.0f}s\n")
    dirs = Counter(r["direction"] for r in out)
    print("1. 두 모델이 얼마나 다른가")
    for k in ("agree", "factor_higher", "factor_lower"):
        print(f"   {k:16s} {dirs[k]:4d}  {dirs[k] / n:6.1%}")

    # A2 — v8 이 미탐 방향(더 높게)으로 이견이면 검수로 보낸다.
    v5_auto = [r for r in out if r["v5_status"] != "needs_review"]
    would_add = [r for r in v5_auto if r["direction"] == "factor_higher"]
    print("\n2. A2(거부 조건)를 걸면")
    print(f"   현재 v5 자동확정      {len(v5_auto):4d} / {n}  {len(v5_auto) / n:6.1%}")
    print(f"   A2 로 검수 이동       {len(would_add):4d}       {len(would_add) / n:6.1%}")
    print(f"   남는 자동확정        {len(v5_auto) - len(would_add):4d} / {n}  "
          f"{(len(v5_auto) - len(would_add)) / n:6.1%}")

    # 3. 그 증가분이 기계 라벨 기준으로 미탐을 잡는가
    def under(r: dict, key: str) -> bool:
        lab = r.get("label")
        return bool(lab) and ORDER.get(r[key], 9) > ORDER.get(lab, 9)

    caught = [r for r in would_add if under(r, "v5")]
    wasted = [r for r in would_add if not under(r, "v5")]
    miss_left = [r for r in v5_auto if under(r, "v5") and r not in would_add]
    print("\n3. 그 증가분이 무엇을 잡는가 (기계 라벨 기준 — 정확도 주장 아님)")
    print(f"   실제 과소분류를 잡음   {len(caught):4d}")
    print(f"   헛수고               {len(wasted):4d}")
    print(f"   여전히 남는 과소분류   {len(miss_left):4d}")
    if would_add:
        print(f"   유효율               {len(caught) / len(would_add):6.1%}")

    print("\n4. 자동확정 안에 남는 과소분류 (기계 라벨 기준)")
    for tag, pool in (("A2 전", v5_auto), ("A2 후", [r for r in v5_auto if r not in would_add])):
        u = [r for r in pool if under(r, "v5")]
        print(f"   {tag}  {len(u):3d}/{len(pool):3d} = {len(u) / max(1, len(pool)):6.2%} "
              f"· 95% 상한 {wilson_upper(len(u), len(pool)):.4f}")

    Path(args.report).write_text(json.dumps({
        "n": n, "directions": dict(dirs),
        "v5_auto": len(v5_auto), "a2_moves": len(would_add),
        "a2_caught": len(caught), "a2_wasted": len(wasted),
        "rows": out,
    }, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ 라벨이 기계 라벨이라 이 수치는 정확도가 아니라 **불일치 분포**다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

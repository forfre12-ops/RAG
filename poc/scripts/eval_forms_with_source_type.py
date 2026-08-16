"""공개 서식에 `source_type=public` 을 주면 헛경보가 실제로 사라지는가.

왜(2026-08-16). 서식을 학습에 넣는 방향은 두 번 다 게이트 FAIL 이었다. 원인은 신호 세기가
아니라 **문서 종류가 두 등급에 걸쳐 있기 때문**이다 - 빈 '이사회 의사록' 양식과 M&A 결정이
적힌 진짜 '이사회 의사록' 은 제목도 형식도 같다. 빈 것만 가르치면 종류 전체를 S3 로 배운다.

대안은 학습이 아니라 **출처를 알려주는 것**이다. `source_type=public` 이면 파이프라인
Gate-1 이 S3 로 cap 한다. 배관은 이미 있는데 **실제로 0% 가 되는지 잰 적이 없다.**

이 스크립트가 답하는 것:
    (1) 메타데이터 없이 서식을 태우면 헛경보가 몇 % 인가        <- 기준선
    (2) source_type=public 을 주면 몇 % 가 되는가              <- 대안의 효과
    (3) 같은 조작이 **진짜 비밀 문서** 에는 어떤 영향인가        <- 부작용 확인

⚠ (3)이 중요하다. cap 이 무차별로 걸리면 진짜 비밀 문서에 잘못된 public 표시가 붙었을 때
  그대로 S3 가 된다 - 미탐이다. 그 위험 크기를 같이 재야 대안을 권할 수 있다.
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

for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        import io as _io
        setattr(sys, _s, _io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))

SEVERE = ("TS", "S1")
NON_PUBLIC = ("TS", "S1", "S2")
ORDER = ["TS", "S1", "S2", "S3"]
RANK = {g: i for i, g in enumerate(ORDER)}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, (c - r) / d), min(1.0, (c + r) / d))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="source_type=public 효과 측정")
    ap.add_argument("--forms", default="datasets/real_s3_forms/sealed_s3.jsonl")
    ap.add_argument("--secret", default="datasets/gold_real/holdout_eval.hardened.jsonl",
                    help="부작용 확인용 - 진짜 비밀 문서에 잘못된 public 표시가 붙으면?")
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_v5_clean/v-fe4b386b")
    ap.add_argument("--report", default="reports/SOURCE_TYPE_PUBLIC.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    os.environ.setdefault("METADATA_FLOOR_ENABLED", "true")
    os.environ["CLASSIFIER_MODEL_DIR"] = args.model_dir

    from koipa.schemas.classify import ClassifyRequest  # noqa: PLC0415
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415

    svc = ClassifyService()

    def run(rows: list[dict], meta: dict | None, tag: str) -> list[dict]:
        out, t0 = [], time.perf_counter()
        for i, r in enumerate(rows):
            text = r.get("text") or r.get("desc") or ""
            try:
                res = svc.classify(ClassifyRequest(doc_id=f"{tag}{i}", content=text,
                                                   metadata=meta, return_evidence=False))
                g = res.label.value if hasattr(res.label, "value") else str(res.label)
                st = res.status
            except Exception as exc:  # noqa: BLE001
                g, st = None, f"error:{type(exc).__name__}"
            out.append({"grade": g, "status": st,
                        "truth": r.get("label") or r.get("expected_grade") or r.get("gold")})
            if (i + 1) % 100 == 0:
                print(f"  [{tag}] {i + 1}/{len(rows)} · {time.perf_counter() - t0:.0f}s")
        return out

    def summarize(preds: list[dict]) -> dict:
        n = len(preds)
        sev = sum(1 for p in preds if p["grade"] in SEVERE)
        npub = sum(1 for p in preds if p["grade"] in NON_PUBLIC)
        rev = sum(1 for p in preds if p["status"] == "needs_review")
        lo, hi = wilson(sev, n)
        return {"n": n, "severe": sev, "severe_rate": round(sev / n, 4) if n else 0,
                "severe_95ci": [round(lo, 4), round(hi, 4)],
                "non_public": npub, "non_public_rate": round(npub / n, 4) if n else 0,
                "review": rev, "review_rate": round(rev / n, 4) if n else 0,
                "dist": dict(Counter(p["grade"] for p in preds))}

    # --- 1) 공개 서식 ---------------------------------------------------------
    forms = [json.loads(l) for l in Path(args.forms).read_text(encoding="utf-8").splitlines()
             if l.strip()]
    print(f"[공개 서식] {len(forms)}건 · 정답 전건 S3\n")
    f_none = summarize(run(forms, None, "f0"))
    f_pub = summarize(run(forms, {"source_type": "public"}, "f1"))

    print(f"\n  {'지표':24s}{'메타 없음':>12s}{'public':>12s}{'변화':>10s}")
    for k, lab in (("severe_rate", "헛경보(TS/S1)"),
                   ("non_public_rate", "비공개 예측(TS/S1/S2)"),
                   ("review_rate", "검수 라우팅")):
        print(f"  {lab:22s}{f_none[k]:>12.1%}{f_pub[k]:>12.1%}{f_pub[k] - f_none[k]:>+10.1%}")
    print(f"  헛경보 95%CI  메타없음 [{f_none['severe_95ci'][0]:.3f}, {f_none['severe_95ci'][1]:.3f}]"
          f"  ->  public [{f_pub['severe_95ci'][0]:.3f}, {f_pub['severe_95ci'][1]:.3f}]")
    print(f"  등급 분포     {f_none['dist']}  ->  {f_pub['dist']}")

    # --- 2) 부작용: 진짜 비밀 문서에 잘못된 public 표시 ------------------------
    sec = [json.loads(l) for l in Path(args.secret).read_text(encoding="utf-8").splitlines()
           if l.strip() and not l.startswith("#")]
    for r in sec:
        r["text"] = r.get("text") or r.get("desc") or ""
    print(f"\n[부작용 확인] 진짜 비밀 문서 {len(sec)}건에 **잘못된** public 표시를 붙이면")
    s_none = run(sec, None, "s0")
    s_pub = run(sec, {"source_type": "public"}, "s1")

    def misses(preds: list[dict]) -> tuple[int, int]:
        """(무음 미탐, 고등급->S3)"""
        silent = sum(1 for p in preds
                     if p["truth"] in RANK and p["grade"] in RANK
                     and RANK[p["grade"]] > RANK[p["truth"]] and p["status"] != "needs_review")
        to_s3 = sum(1 for p in preds
                    if p["truth"] in SEVERE and p["grade"] == "S3")
        return silent, to_s3

    m0, t0_ = misses(s_none)
    m1, t1_ = misses(s_pub)
    print(f"  무음 미탐      {m0}건 -> {m1}건")
    print(f"  고등급->S3     {t0_}건 -> {t1_}건")
    exact0 = sum(1 for p in s_none if p["grade"] == p["truth"])
    exact1 = sum(1 for p in s_pub if p["grade"] == p["truth"])
    print(f"  정답 일치      {exact0}/{len(sec)} -> {exact1}/{len(sec)}")

    payload = {"model_dir": args.model_dir,
               "forms": {"path": args.forms, "no_meta": f_none, "source_public": f_pub},
               "secret_side_effect": {
                   "path": args.secret, "n": len(sec),
                   "silent_miss": [m0, m1], "high_to_s3": [t0_, t1_],
                   "exact": [exact0, exact1]},
               "note": "정답은 서식 전건 S3. 부작용 측정은 '잘못된 public 표시' 가정이다."}
    Path(args.report).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ 부작용 수치는 **표시가 틀렸을 때** 의 값이다. 맞게 붙이면 이런 일은 없다.")
    print("  다만 그 표시를 누가 붙이는가가 이 대안의 전제다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

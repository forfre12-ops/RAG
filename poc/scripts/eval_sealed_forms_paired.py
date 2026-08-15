"""봉인 서식 400건에서 **같은 문서**로 baseline 과 candidate 를 나란히 잰다.

왜 별도 스크립트인가(외부 검토 지적). `eval_public_form_specificity.py` 는 원본 폴더를
다시 표집하고 `--unseal` 은 work·sealed 를 **둘 다** 포함한다. "봉인만 평가" 하는 옵션이
아니다. 그리고 기존 161건 13.0% 와 새 400건 결과를 비교하면 표본 차이가 섞인다.

그래서 이 스크립트는
    입력    datasets/real_s3_forms/sealed_s3.jsonl 하나만
    방식    같은 400건을 두 모델에 태워 **문서별로 짝지어** 비교
    검정    McNemar (짝지은 이항 검정) - 한쪽만 틀린 문서 수로 판정한다

세 지표를 모두 낸다(정의가 코드마다 달라 혼동이 있었다).
    severe      TS 또는 S1 로 예측        = 영업비밀이라 부름. 가장 나쁜 헛경보
    non_public  TS·S1·S2 로 예측          = 공개문서가 아니라고 봄
    review      needs_review 로 라우팅     = 검수 부담

⚠ 정답은 전건 S3 다(공개 배포되는 빈 서식). 그래서 **특이도만** 잴 수 있고 등급 정확도는
  못 잰다. S2 냐 S3 냐는 취급 규칙 문제이지 이 셋이 답할 수 있는 질문이 아니다.

⚠ 폴더별로도 낸다. 한 폴더의 큰 악화를 합계 개선으로 숨기지 않기 위해서다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
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


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, (c - r) / d), min(1.0, (c + r) / d))


def mcnemar(b: int, c: int) -> float:
    """짝지은 이항 검정의 정확 양측 p-값. b·c 는 한쪽만 해당한 문서 수.

    표본이 작을 수 있어 카이제곱 근사 대신 이항 정확검정을 쓴다.
    """
    n = b + c
    if n == 0:
        return 1.0
    from math import comb  # noqa: PLC0415
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def run(model_dir: str, rows: list[dict], tag: str) -> list[dict]:
    os.environ["CLASSIFIER_MODEL_DIR"] = model_dir
    # 모듈이 설정을 기동 시 읽으므로 서브프로세스 대신 매번 서비스를 새로 만든다.
    from koipa.schemas.classify import ClassifyRequest  # noqa: PLC0415
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415

    svc = ClassifyService()
    out, t0 = [], time.perf_counter()
    for i, r in enumerate(rows):
        try:
            res = svc.classify(ClassifyRequest(doc_id=r["doc_id"], content=r["text"],
                                               return_evidence=False))
            g = res.label.value if hasattr(res.label, "value") else str(res.label)
            st = res.status
        except Exception as exc:  # noqa: BLE001
            g, st = None, f"error:{type(exc).__name__}"
        out.append({"doc_id": r["doc_id"], "source": r.get("source"), "grade": g, "status": st})
        if (i + 1) % 100 == 0:
            print(f"  [{tag}] {i + 1}/{len(rows)} · {time.perf_counter() - t0:.0f}s")
    return out


def summarize(preds: list[dict]) -> dict:
    n = len(preds)
    sev = sum(1 for p in preds if p["grade"] in SEVERE)
    npub = sum(1 for p in preds if p["grade"] in NON_PUBLIC)
    rev = sum(1 for p in preds if p["status"] == "needs_review")
    return {
        "n": n,
        "severe": sev, "severe_rate": round(sev / n, 4) if n else 0,
        "non_public": npub, "non_public_rate": round(npub / n, 4) if n else 0,
        "review": rev, "review_rate": round(rev / n, 4) if n else 0,
        "dist": dict(Counter(p["grade"] for p in preds)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="봉인 서식 paired 비교")
    ap.add_argument("--sealed", default="datasets/real_s3_forms/sealed_s3.jsonl")
    ap.add_argument("--baseline", default="artifacts/classifier_p1_v5_clean/v-fe4b386b")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--report", default="reports/SEALED_FORMS_PAIRED.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    os.environ.setdefault("METADATA_FLOOR_ENABLED", "true")

    rows = [json.loads(l) for l in Path(args.sealed).read_text(encoding="utf-8").splitlines()
            if l.strip()]
    print(f"봉인 서식 {len(rows)}건 · 정답 전건 S3\n")

    base = run(args.baseline, rows, "baseline")
    cand = run(args.candidate, rows, "candidate")

    sb, sc = summarize(base), summarize(cand)
    print(f"\n{'지표':22s}{'baseline':>12s}{'candidate':>12s}{'변화':>10s}")
    for key, label in (("severe_rate", "헛경보(TS/S1)"),
                       ("non_public_rate", "비공개 예측(TS/S1/S2)"),
                       ("review_rate", "검수 라우팅")):
        d = sc[key] - sb[key]
        print(f"  {label:20s}{sb[key]:>12.1%}{sc[key]:>12.1%}{d:>+10.1%}")

    # paired: 문서별 전환
    bym = {p["doc_id"]: p for p in base}
    both = [(bym[c["doc_id"]], c) for c in cand if c["doc_id"] in bym]
    print(f"\n짝지은 비교 (같은 문서 {len(both)}건)")
    paired = {}
    for key, fn in (("severe", lambda p: p["grade"] in SEVERE),
                    ("non_public", lambda p: p["grade"] in NON_PUBLIC),
                    ("review", lambda p: p["status"] == "needs_review")):
        b_only = sum(1 for b, c in both if fn(b) and not fn(c))     # 고쳐짐
        c_only = sum(1 for b, c in both if not fn(b) and fn(c))     # 나빠짐
        p = mcnemar(b_only, c_only)
        lo, hi = wilson(sum(1 for _, c in both if fn(c)), len(both))
        paired[key] = {"fixed": b_only, "worsened": c_only, "p_value": round(p, 5),
                       "candidate_95ci": [round(lo, 4), round(hi, 4)]}
        print(f"  {key:12s} 고쳐짐 {b_only:>4d} · 나빠짐 {c_only:>4d} · p={p:.4f} "
              f"· 후보 95%CI [{lo:.3f}, {hi:.3f}]")

    # 폴더별 - 합계 개선이 특정 폴더 악화를 숨기지 않게
    print(f"\n폴더별 헛경보(TS/S1)")
    byf = defaultdict(lambda: {"n": 0, "b": 0, "c": 0})
    for b, c in both:
        f = b.get("source") or "?"
        byf[f]["n"] += 1
        byf[f]["b"] += 1 if b["grade"] in SEVERE else 0
        byf[f]["c"] += 1 if c["grade"] in SEVERE else 0
    folders = {}
    for f, v in sorted(byf.items()):
        rb, rc = v["b"] / v["n"], v["c"] / v["n"]
        folders[f] = {**v, "base_rate": round(rb, 4), "cand_rate": round(rc, 4)}
        flag = "  <- 악화" if rc > rb else ""
        print(f"  {f[:24]:26s} n={v['n']:>3d}  {rb:>6.1%} -> {rc:>6.1%}{flag}")

    payload = {"sealed": args.sealed, "baseline": args.baseline, "candidate": args.candidate,
               "baseline_summary": sb, "candidate_summary": sc,
               "paired": paired, "by_folder": folders,
               "note": "정답 전건 S3. 특이도만 판정 가능하며 등급 정확도는 못 잰다."}
    Path(args.report).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

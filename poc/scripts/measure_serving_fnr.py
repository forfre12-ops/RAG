"""서빙 경로 **전체**를 통과시켜 무음 미탐률을 잰다 — 모델 단독 수치와 다른 값이다.

왜 필요한가. `score_model_on_eval.py` 는 M5 **집계**까지만 태운다(청크→윈도→온도→
길이가중→argmax). 거기서 나온 고등급 FNR 은 v6 40.3% · v5 54.7% 였다. 그런데 그 경로는
**post-model 서빙 가드를 타지 않는다** — FNR-safe override · source-prior cap ·
metadata floor · escalation tau · 합의 게이트. 계약이 명시적으로 제외하는 항목들이다.

기록상 v4 에서 원시 argmax FNR 0.167 → 서빙 경로 0.018 로 **약 9배** 차이가 났다.
그러니 모델 단독 40% 를 "운영에서 10건 중 4건을 놓친다"로 읽으면 틀린다.

**이 스크립트가 세는 것은 다르다.** 고등급 문서가 낮은 등급으로 갔더라도 `needs_review`
로 라우팅됐으면 사람이 본다 — 그건 미탐이 아니라 **잡힌 것**이다. 놓친 것은
**낮게 가고 자동확정까지 된 것**뿐이다:

    무음 미탐 = 정답 고등급(TS·S1) ∧ 예측 더 낮음 ∧ status ≠ needs_review

이 값이 본 사업 1차 목표(미탐 최소화)의 실제 지표다.

실행 중인 API 를 쓴다 — 파이프라인을 재구성하면 그게 서빙 경로라는 보장이 없다.
doc_id 를 비-UUID 로 주므로 DB 에 persist 되지 않는다(운영 데이터 오염 없음).

사용:
    python scripts/measure_serving_fnr.py \
        --eval datasets/proxy_eval/direct_authored_proxy_eval_split.v3/final_800.locked.jsonl \
        --api http://127.0.0.1:8000 --high-only
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

GRADES = ("TS", "S1", "S2", "S3")
ORDER = {g: i for i, g in enumerate(GRADES)}      # TS=0 이 가장 높다
HIGH = ("TS", "S1")


def _read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def classify(api: str, key: str, doc_id: str, text: str, timeout: int) -> dict | None:
    body = json.dumps({"doc_id": doc_id, "content": text}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{api}/api/v1/classify",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8", "X-API-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="서빙 경로 무음 미탐률 측정")
    parser.add_argument("--eval", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default=None, help="없으면 KOIPA_API_KEY 환경변수")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--high-only", action="store_true",
                        help="고등급(TS·S1)만 — FNR 만 필요할 때 시간을 아낀다")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    import os

    key = args.api_key or os.environ.get("KOIPA_API_KEY", "")
    if not key:
        raise SystemExit("API 키가 필요하다 (--api-key 또는 KOIPA_API_KEY)")

    rows = _read_jsonl(Path(args.eval))
    if args.high_only:
        rows = [r for r in rows if str(r.get("label")) in HIGH]
    if args.limit:
        rows = rows[: args.limit]
    print(f"[eval] {args.eval} · {len(rows)}건 (high_only={args.high_only})", flush=True)

    started = time.time()
    records: list[dict] = []
    failed = 0
    for index, row in enumerate(rows):
        truth = str(row.get("label"))
        result = classify(args.api, key, f"servingfnr-{index:05d}", str(row.get("text") or ""),
                          args.timeout)
        if result is None:
            failed += 1
            continue
        predicted = str(result.get("predicted_level") or result.get("label") or "")
        status = str(result.get("status") or "")
        records.append({
            "truth": truth,
            "predicted": predicted,
            "status": status,
            "confidence": result.get("confidence"),
            "warnings": result.get("warnings") or [],
        })
        if (index + 1) % 25 == 0:
            print(f"  {index+1}/{len(rows)} · {time.time()-started:.0f}s", flush=True)

    high = [r for r in records if r["truth"] in HIGH]
    def lower(r):
        return r["predicted"] in ORDER and ORDER[r["predicted"]] > ORDER[r["truth"]]

    underclassified = [r for r in high if lower(r)]
    silent = [r for r in underclassified if r["status"] != "needs_review"]
    caught = [r for r in underclassified if r["status"] == "needs_review"]

    report = {
        "eval_set": args.eval,
        "api": args.api,
        "scored": len(records),
        "failed_requests": failed,
        "scoring_path": "FULL serving path via POST /api/v1/classify "
                        "(post-model guards INCLUDED: FNR-safe override, source-prior cap, "
                        "metadata floor, escalation tau, agreement gate)",
        "high_grade_documents": len(high),
        "underclassified": len(underclassified),
        "silent_miss": len(silent),
        "caught_by_review": len(caught),
        "silent_miss_rate": round(len(silent) / len(high), 4) if high else 0.0,
        "underclass_rate_before_guards": round(len(underclassified) / len(high), 4) if high else 0.0,
        "status_distribution": dict(sorted(Counter(r["status"] for r in records).items())),
        # 어느 게이트가 검수로 보냈나. 자동확정률이 낮을 때 **무엇을 고쳐야 하는지**는
        # 이 분포가 정한다 — 라우팅 경로가 12종이라 합계만 봐서는 알 수 없다.
        "review_reason_counts": dict(
            sorted(
                Counter(
                    w.split(":")[0]
                    for r in records if r["status"] == "needs_review"
                    for w in r["warnings"] if ":" in w
                ).items(),
                key=lambda kv: -kv[1],
            )
        ),
        "auto_confirm_rate": round(
            sum(1 for r in records if r["status"] != "needs_review") / max(len(records), 1), 4
        ),
        "predicted_distribution": dict(sorted(Counter(r["predicted"] for r in records).items())),
        "note": (
            "silent_miss 만이 운영상 놓친 것이다. needs_review 로 간 것은 사람이 본다. "
            "이 값을 모델 단독 FNR(score_model_on_eval)과 나란히 놓지 말 것 - 다른 경로다."
        ),
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        # 문서별 원자료를 함께 남긴다 — 집계만 남기면 임계 스윕 같은 사후 분석을 하려고
        # 800건(30분)을 다시 돌려야 한다. 실제로 한 번 그랬다. 한 번 재고 여러 번 분석한다.
        records_path = out.with_name(out.stem + ".records.jsonl")
        records_path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
    print(json.dumps({k: v for k, v in report.items() if k != "note"},
                     ensure_ascii=True, indent=2))
    if args.out:
        print(f"[report] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

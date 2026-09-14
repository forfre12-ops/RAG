#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""출처 메타데이터를 주면 공개문서 과탐이 얼마나 줄어드는가 — 같은 문서를 두 번 태워 잰다.

## 왜 (2026-09-14)

오늘 공개 보도자료 300건을 서빙 경로에 태웠더니 **98건(32.7%)을 고등급(TS·S1)** 이라
불렀다(감리 185(가)가 지목한 축). 그런데 그 측정은 `{doc_id, content}` 만 보냈다 —
**메타데이터를 안 보냈다.**

배포본에는 출처 cap 이 이미 켜져 있다(`source_prior_enabled=True`,
`source_prior_cap_grade=S3`). 발동 조건은 `metadata.source_type` 이 공개 출처
토큰과 맞는 것이다(pipeline.py `_source_prior_is_public`). 즉 **KL 이 ICD 대로
source_type 을 보내면 이 문서들은 S3 로 cap 된다.** 그런데 실제 공급률은
`source_type 0/164,587 = 0.00%` 다.

그래서 이 도구는 "메타데이터를 주세요" 라는 막연한 요청을
**"주면 과탐 32.7% 가 몇 %로 떨어진다"** 는 숫자로 바꾼다.

⚠ 같은 문서를 두 번 보내되 **바꾸는 축은 metadata 하나**다. 모델·설정·본문은 그대로다.

사용:
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/measure_source_prior_effect.py \
        --eval datasets/proxy_gold/public_s3_challenges/public-s3-300-20260808-v1/records.jsonl \
        --source-type 보도자료 --api http://127.0.0.1:8005 \
        --out reports/SOURCE_PRIOR_EFFECT.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "src"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

GRADE_ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
HIGH = ("TS", "S1")
TEXT_KEYS = ("text", "content", "body")
LABEL_KEYS = ("label", "target", "grade", "gold", "y")


def _post(api: str, key: str, doc_id: str, text: str, metadata: dict | None,
          fails: Counter, timeout: float = 60.0) -> dict | None:
    body_obj: dict = {"doc_id": doc_id, "content": text}
    if metadata:
        body_obj["metadata"] = metadata
    body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
    for attempt in range(6):
        req = urllib.request.Request(
            f"{api}/api/v1/classify", data=body,
            headers={"Content-Type": "application/json; charset=utf-8", "X-API-Key": key},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = float(wait) if wait else 0.0
                except (TypeError, ValueError):
                    delay = 0.0
                fails["429 한도초과(대기 후 재시도)"] += 1
                time.sleep(max(delay, 5.0 if attempt == 0 else 15.0))
                continue
            fails[f"HTTP {exc.code}"] += 1
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            fails[type(exc).__name__] += 1
            return None
    fails["429 재시도 소진"] += 1
    return None


def _rows(path: Path):
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _pick(row: dict, keys) -> str:
    for k in keys:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def summarize(recs: list[dict]) -> dict:
    lo = [r for r in recs if r["truth"] in ("S2", "S3")]
    over = [r for r in lo if GRADE_ORDER.get(r["pred"], 9) < GRADE_ORDER.get(r["truth"], 9)]
    severe = [r for r in lo if r["pred"] in HIGH]
    auto_over = [r for r in over if r["status"] != "needs_review"]
    review = [r for r in recs if r["status"] == "needs_review"]
    n = len(lo) or 1
    return {
        "n_low": len(lo),
        "overclass": len(over), "overclass_rate": len(over) / n,
        "severe": len(severe), "severe_rate": len(severe) / n,
        "auto_overclass": len(auto_over), "auto_overclass_rate": len(auto_over) / n,
        "review_load": len(review), "review_rate": len(review) / (len(recs) or 1),
        "predicted": dict(Counter(r["pred"] for r in recs).most_common()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--api", default="http://127.0.0.1:8005")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--source-type", required=True,
                    help="metadata.source_type 에 넣을 값. 문서 성격과 맞아야 한다")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import os
    key = args.api_key or os.environ.get("KOIPA_API_KEY", "")
    rows = list(_rows(POC / args.eval))
    if args.limit:
        rows = rows[:args.limit]

    fails: Counter = Counter()
    without: list[dict] = []
    with_md: list[dict] = []
    print(f"대상 {len(rows)}건 · 축 하나만 바꾼다(metadata.source_type={args.source_type!r})")

    for i, row in enumerate(rows, 1):
        text = _pick(row, TEXT_KEYS)
        truth = _pick(row, LABEL_KEYS)
        if not text or not truth:
            fails["본문·정답 없음"] += 1
            continue
        did = f"sp-{i:05d}"
        a = _post(args.api, key, did + "-a", text, None, fails)
        b = _post(args.api, key, did + "-b", text, {"source_type": args.source_type}, fails)
        if not a or not b:
            continue
        without.append({"truth": truth, "pred": a.get("label"), "status": a.get("status"),
                        "warnings": a.get("warnings") or []})
        with_md.append({"truth": truth, "pred": b.get("label"), "status": b.get("status"),
                        "warnings": b.get("warnings") or []})
        if i % 50 == 0:
            print(f"  … {i}/{len(rows)}")

    if not without:
        print("채점된 것이 없다 — API·키를 확인하라")
        print("실패:", dict(fails))
        return 1

    a_sum, b_sum = summarize(without), summarize(with_md)
    capped = sum(1 for x, y in zip(without, with_md) if x["pred"] != y["pred"])
    cap_warn = sum(1 for y in with_md if any("source-prior" in str(w) for w in y["warnings"]))

    print()
    print("=" * 78)
    # 재시도 횟수와 '유실' 을 같은 칸에 적으면 채점 안 된 문서가 있는 것처럼 읽힌다.
    retried = sum(v for k, v in fails.items() if "재시도" in k or "한도초과" in k)
    lost = len(rows) - len(without)
    print(f"출처 메타데이터 효과 — 채점 {len(without)}/{len(rows)}건 · "
          f"유실 {lost}건 · 429 재시도 {retried}회")
    print("=" * 78)
    print(f"{'':22} {'메타 없음':>10} {'메타 있음':>10}   변화")
    for label, k in (("과탐", "overclass_rate"), ("격상(TS·S1)", "severe_rate"),
                     ("자동확정 과탐", "auto_overclass_rate"), ("검수율", "review_rate")):
        x, y = a_sum[k] * 100, b_sum[k] * 100
        print(f"  {label:<20} {x:9.1f}% {y:9.1f}%   {y - x:+6.1f}%p")
    print()
    print(f"  예측이 바뀐 문서 {capped}건 · source-prior 경고가 붙은 문서 {cap_warn}건")
    print(f"  메타 없음 예측분포 {a_sum['predicted']}")
    print(f"  메타 있음 예측분포 {b_sum['predicted']}")
    if fails:
        print(f"\n  ⚠ 실패 내역 {dict(fails)}")

    out = {
        "eval_set": args.eval, "n_requested": len(rows), "n_scored": len(without),
        "n_lost": len(rows) - len(without), "source_type": args.source_type,
        "without_metadata": a_sum, "with_metadata": b_sum,
        "changed_predictions": capped, "source_prior_warned": cap_warn,
        "failures": dict(fails),
        "note": "축은 metadata 하나만 바꿨다. 모델·설정·본문 동일.",
    }
    if args.out:
        p = POC / args.out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {p.relative_to(POC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

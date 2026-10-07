"""검수 임계(review_confidence_threshold)를 근거 있는 값으로 다시 잡는다.

왜 필요한가(실측 2026-08-12). 배포본이 평가셋 800건 **전부**를 `needs_review` 로 보냈고
(자동확정률 0.0%), 사유는 150/150 이 `low-confidence` 였다. 원인은 모델 성능이 아니라
**설정 불일치**다:

    배포본 온도  T = 2.03   (OOD 과신을 막으려고 의도적으로 높인 값)
    실측 confidence  0.322 ~ 0.456 (중앙 0.353)
    임계          0.70      ← 미보정(T=1) 시절 값으로 보인다
    → 4등급에서 T=2.03 이면 최대 confidence 가 구조적으로 0.70 에 못 미친다

온도는 안전장치라 낮추면 안 된다(과신 복귀). **임계를 보정 후 스케일로 다시 잡는 것이
맞다.** 그런데 임계를 내리면 자동확정이 열리는 대신 **무음 미탐이 생길 수 있다** —
그 교환을 눈으로 보고 정해야 한다.

이 스크립트는 판정하지 않는다. 임계마다 세 값을 같이 낸다:

    자동확정률        얼마나 자동화되나
    자동확정 정밀도    자동확정된 것 중 맞은 비율
    **무음 미탐**      고등급인데 낮게 가고 자동확정까지 된 건수  ← 이게 0 이어야 한다

본 사업 1차 목표가 미탐 최소화이므로 **무음 미탐 0 을 유지하는 최대 임계 완화폭**이
답이다. 정밀도나 자동확정률로 고르는 것이 아니다.

입력은 measure_serving_fnr.py 가 남긴 `*.records.jsonl` 이다(문서별 정답·예측·confidence).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

try:  # [2026-08-22] cp949 콘솔에서 아래 경고 한 줄이 UnicodeEncodeError 로 죽었다.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

GRADES = ("TS", "S1", "S2", "S3")
ORDER = {g: i for i, g in enumerate(GRADES)}
HIGH = ("TS", "S1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="검수 임계 스윕")
    parser.add_argument("--records", required=True, help="measure_serving_fnr 의 *.records.jsonl")
    parser.add_argument("--current", type=float, default=0.70)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    rows = [
        json.loads(line)
        for line in Path(args.records).read_text("utf-8").splitlines()
        if line.strip()
    ]
    scored = [r for r in rows if r.get("confidence") is not None]
    print(f"[records] {len(scored)}건")
    confs = sorted(float(r["confidence"]) for r in scored)
    print(f"[confidence] {confs[0]:.3f} ~ {confs[-1]:.3f} · 중앙 {confs[len(confs)//2]:.3f}")

    def evaluate(threshold: float) -> dict:
        auto = [r for r in scored if float(r["confidence"]) >= threshold]
        correct = sum(1 for r in auto if r["predicted"] == r["truth"])
        silent = [
            r for r in auto
            if r["truth"] in HIGH
            and r["predicted"] in ORDER
            and ORDER[r["predicted"]] > ORDER[r["truth"]]
        ]
        return {
            "threshold": round(threshold, 3),
            "auto_confirmed": len(auto),
            "auto_confirm_rate": round(len(auto) / len(scored), 4),
            "auto_precision": round(correct / len(auto), 4) if auto else None,
            "silent_miss": len(silent),
        }

    grid = [round(x / 100, 2) for x in range(20, 76, 5)]
    results = [evaluate(t) for t in grid]

    print(f"\n{'임계':>6} {'자동확정':>9} {'자동확정률':>10} {'자동확정정밀도':>12} {'무음미탐':>8}")
    print("-" * 52)
    for r in results:
        prec = f"{r['auto_precision']:.3f}" if r["auto_precision"] is not None else "  -  "
        mark = "  <- 현행" if abs(r["threshold"] - args.current) < 1e-9 else ""
        flag = "  !!" if r["silent_miss"] else ""
        print(f"{r['threshold']:>6.2f} {r['auto_confirmed']:>9d} "
              f"{r['auto_confirm_rate']:>10.1%} {prec:>12} {r['silent_miss']:>8d}{flag}{mark}")

    safe = [r for r in results if r["silent_miss"] == 0]
    best = min(safe, key=lambda r: r["threshold"]) if safe else None
    print("\n[판단 기준] 무음 미탐 0 을 유지하는 **가장 낮은** 임계 = 자동화 최대")
    if best:
        print(f"  권고 임계 {best['threshold']:.2f} · "
              f"자동확정률 {best['auto_confirm_rate']:.1%} · "
              f"정밀도 {best['auto_precision']}")
    else:
        print("  [경고] 어떤 임계에서도 무음 미탐이 0 이 아니다 - 임계 조정으로 풀 문제가 아니다")

    report = {
        "records": args.records,
        # [2026-08-22] 입력 파일 해시를 같이 남긴다. 종전엔 경로만 적었는데, 뒤 실행이 같은
        # 이름으로 레코드를 덮어써서 **이 리포트가 가리키는 파일의 내용이 스윕 수치와 달라진**
        # 일이 있었다(hardened42 t203: 21/21 로 계산한 곡선이 27/15 짜리 파일을 가리켰다).
        # 해시가 있으면 포인터가 어긋난 것을 읽는 쪽이 바로 안다.
        "records_sha256": hashlib.sha256(Path(args.records).read_bytes()).hexdigest(),
        "current_threshold": args.current,
        "confidence_range": [confs[0], confs[-1]],
        "sweep": results,
        "recommended": best,
        "criterion": "무음 미탐 0 을 유지하는 최소 임계. 정밀도·자동확정률로 고르지 않는다 "
                     "- 본 사업 1차 목표가 미탐 최소화이기 때문이다.",
        # [2026-08-22] 종전엔 "v3 final_800 기준"이 문자열로 박혀 있었다 - 다른 셋으로 돌려도
        # 그 문구가 그대로 나가서 리포트가 자기 입력을 잘못 설명했다(hardened42 스윕이 그랬다).
        "caveat": f"이 곡선은 {args.records} 한 셋(합성 평가셋) 기준이다. 실 운영 분포에서는 "
                  "다르게 나온다. 임계를 운영에 반영하기 전에 회원사 분포에서 다시 재야 한다.",
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"[report] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

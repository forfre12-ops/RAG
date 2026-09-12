"""임계 그리드 실측 레코드를 한 표로 모은다 — 자동확정률 · 정밀도 · **두 종류의 과소분류**.

왜 두 종류인가(2026-08-23). sweep_review_threshold.py 의 '무음 미탐'은 정답이 고등급
(TS·S1)인 건만 센다. 그 정의로는 **S2 문서가 S3(공개)로 자동확정된 건**이 0 으로 잡힌다.
holdout109 에서 실제로 그런 건이 2 건 있었고(conf 0.678 · 0.613), 현행 임계 0.70 이
바로 그 둘을 검수로 잡고 있었다. 임계를 내릴지 판단하려면 이 둘을 갈라서 봐야 한다.

    무음 미탐(계약)   정답 TS·S1 → 더 낮은 등급 → 자동확정   ← 본 사업 1차 목표 지표
    과소분류 자동확정  정답 TS·S1·S2 → 더 낮은 등급 → 자동확정 (공개 오태깅 포함)

입력은 measure_serving_records.py 가 남긴 *.records.jsonl 이다.

사용:
    python scripts/analyze_threshold_grid.py --glob "reports/thresh_revalidation/hardened42_*.records.jsonl"
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
HIGH = ("TS", "S1")
UNDER = ("TS", "S1", "S2")   # S3 는 더 낮을 수 없다


def _thresholds_of(path: Path) -> tuple[float | None, float | None]:
    """(일반 임계, 공개등급 임계). 옆에 있는 리포트 json 의 유효설정을 먼저 읽는다.

    파일명 규칙(_th0.55)에만 기대면 등급차등 런(_split…)이 '임계 미상'으로 떨어져서
    표에서 0.70 런과 구분되지 않는다. 리포트에는 실제로 서빙에 선 값이 settings_effective
    로 남아 있으니 그쪽이 정답이다.
    """
    report = path.with_name(path.name.replace(".records.jsonl", ".json"))
    if report.is_file():
        try:
            eff = json.loads(report.read_text("utf-8")).get("settings_effective") or {}
            base = eff.get("review_confidence_threshold")
            pub = eff.get("review_confidence_threshold_public")
            if base is not None:
                return float(base), (None if pub is None else float(pub))
        except Exception:  # noqa: BLE001
            pass
    m = re.search(r"_th([\d.]+)\.records\.jsonl$", path.name)
    return (float(m.group(1)) if m else None), None


def _rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text("utf-8").splitlines() if l.strip()]


def summarize(path: Path) -> dict:
    rows = _rows(path)
    base, pub = _thresholds_of(path)
    auto = [r for r in rows if r.get("status") != "needs_review"]
    correct = sum(1 for r in auto if r["predicted"] == r["truth"])

    def lower(r: dict) -> bool:
        return (r["predicted"] in ORDER and r["truth"] in ORDER
                and ORDER[r["predicted"]] > ORDER[r["truth"]])

    silent_high = [r for r in auto if r["truth"] in HIGH and lower(r)]
    silent_all = [r for r in auto if r["truth"] in UNDER and lower(r)]
    to_public = [r for r in silent_all if r["predicted"] == "S3"]
    return {
        "file": str(path),
        "threshold": base,
        "threshold_public": pub,
        "n": len(rows),
        "auto_confirmed": len(auto),
        "auto_confirm_rate": round(len(auto) / len(rows), 4) if rows else None,
        "auto_precision": round(correct / len(auto), 4) if auto else None,
        "silent_miss_high": len(silent_high),
        "silent_miss_any": len(silent_all),
        "auto_tagged_public": len(to_public),
        "review_count": len(rows) - len(auto),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="임계 그리드 요약")
    ap.add_argument("--glob", required=True, action="append",
                    help="records.jsonl 글롭. 반복 가능")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    paths: list[Path] = []
    for g in args.glob:
        paths.extend(Path(p) for p in sorted(_glob.glob(g)))
    if not paths:
        raise SystemExit("레코드 파일을 찾지 못했다")

    out = [summarize(p) for p in paths]
    out.sort(key=lambda r: (r["file"].rsplit("_", 1)[0], r["threshold"] or 0.70,
                            r["threshold_public"] or 0.0))

    header = (f"{'임계 일반/공개':>14} {'자동확정':>9} {'자동확정률':>10} {'정밀도':>8} "
              f"{'무음미탐(TS·S1)':>15} {'과소분류자동확정':>16} {'공개오태깅':>10}  파일")
    print(header)
    print("-" * len(header))
    for r in out:
        _b = f"{r['threshold']:.2f}" if r["threshold"] is not None else "?"
        th = _b if r["threshold_public"] is None else f"{_b}/{r['threshold_public']:.2f}"
        prec = f"{r['auto_precision']:.4f}" if r["auto_precision"] is not None else "  -  "
        print(f"{th:>14} {r['auto_confirmed']:>9d} {r['auto_confirm_rate']:>10.1%} {prec:>8} "
              f"{r['silent_miss_high']:>15d} {r['silent_miss_any']:>16d} "
              f"{r['auto_tagged_public']:>10d}  {Path(r['file']).name}")
    print("")
    print("임계 a/b = 일반 a · 공개등급 예측 b(등급차등). 단일 값이면 등급차등 미적용.")
    print("무음미탐(TS·S1) = 계약 1차 목표 지표 · 과소분류자동확정 = S2→S3 등 공개 오태깅 포함")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps({"rows": out}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[report] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

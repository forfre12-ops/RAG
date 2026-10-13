"""길이가 등급을 알려주지 않는 홀드아웃을 만든다 — 등급 × 길이대 층화 추출.

왜 필요한가(2026-09-05). 제출 수치가 걸린 홀드아웃 넷이 전부 계보 검사에서
usable_for_comparison=false 다. 오염을 걷어내도(clean_holdout_leakage) 풀리지 않았다 —
막고 있던 것은 **길이가 등급을 알려주는 것**이었기 때문이다.

    hardened42   Theil's U 0.436     S3 7건이 2,323~3,023자 · 나머지는 151~317자
    clean42                0.507
    holdout109             0.372
    v5 test                0.283
    v6 test                0.328     판례를 전부 S3 로 모으면 오히려 나빠진다

길이가 갈리는 이유는 구조적이다 — **공개 판결문은 길고, 규칙상 정의로 S3 다.** 그래서
문서를 몇 건 빼는 것으로는 안 되고, 등급마다 길이 분포가 겹치도록 **다시 뽑아야** 한다.

이 스크립트가 하는 것
    1. 길이대(버킷)로 나눈다. 기본 경계 300 · 600 · 1,000 · 2,000자
    2. **네 등급이 모두 있는 버킷만** 쓴다. 2,000자 이상은 S3 뿐이라 버린다
    3. 버킷마다 등급별로 같은 수씩 뽑는다(--ratio-cap 으로 배수 허용)
    4. 학습셋과 같은 원본인 문서는 뺀다(clean_holdout_leakage 와 같은 판정)

실측 효과 (v6 셋 · val+test 498행에서 추출)

    구성                 n     문장공유   길이-only   Theil's U
    v6 test 그대로     249     0.1647     0.502      0.328
    배수<=1.0          135     0.1185     0.274      0.073
    배수<=1.5          178     0.1236     0.242      0.074      <- 무작위 0.25 보다 낮다
    배수<=2.0          216     0.1157     0.287      0.074

⚠ **이것으로 usable_for_comparison 이 true 가 되지는 않는다.** 길이 축은 풀리지만 문장
  공유가 남는다. 실측으로 공유 20종을 전부 읽었더니 같은 원본은 하나도 없었고 전부 정형
  문구였다(판결문 서식 6 · 생성기 템플릿 9 · 보고서 서식 1). 한국어 판례가 든 홀드아웃은
  어떻게 만들어도 판결문 맺음말을 공유한다.

  그 문턱을 풀지 말지는 **사람이 정한다.** 이 스크립트는 수치를 만들 뿐이다.
  koipa.holdout_independence 가 상투어를 뺀 값을 함께 내므로 그것을 근거로 삼을 것.

⚠ 뽑은 셋을 배포 모델 비교에 쓰기 전에 report_holdout_independence.py --strict 를 돌릴 것.
  통과해도 나올 수 있는 주장은 "합성 내부 일관성 + 안전 무회귀"까지다.

usage:
  # 모델 둘을 견줄 때는 **양쪽 학습셋을 모두** 준다 — 한쪽만 보면 다른 모델이 외운
  # 문서가 남아 그 모델에 유리해진다.
  python scripts/build_balanced_holdout.py \
      --train datasets/labeled_p1_v6_court/train.jsonl \
      --train datasets/labeled_p1_v5_clean/train.jsonl \
      --pool  datasets/labeled_p1_v6_court/val.jsonl,datasets/labeled_p1_v6_court/test.jsonl \
      --out   datasets/labeled_p1_v6_court/holdout_balanced.jsonl
  python scripts/build_balanced_holdout.py ... --dry-run     # 수치만 본다
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from collections import defaultdict
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from clean_holdout_leakage import TrainIndex, leak_reason, sha, text_of  # noqa: E402
from koipa.holdout_independence import assess  # noqa: E402

GRADES = ("TS", "S1", "S2", "S3")
DEFAULT_EDGES = "300,600,1000,2000"
# 이 수 미만인 칸은 표본이 모자라 균형의 의미가 없다.
MIN_PER_CELL = 2


def _load(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _key(record: dict) -> str:
    """결정적 선택 순서 — 시각·난수를 쓰지 않는다(같은 입력이면 같은 셋)."""
    return hashlib.sha1((record.get("text") or "").encode("utf-8")).hexdigest()


def _bucket(length: int, edges: list[int]) -> int | None:
    """길이대 번호. 마지막 경계 이상은 None — 그 구간은 한 등급뿐이라 쓰지 않는다."""
    lo = 0
    for i, hi in enumerate(edges):
        if lo <= length < hi:
            return i
        lo = hi
    return None


def pick(
    pool: list[dict],
    edges: list[int],
    *,
    ratio_cap: float,
) -> tuple[list[dict], list[dict]]:
    """(뽑힌 행, 버킷별 진단). 네 등급이 모두 찬 버킷에서만 뽑는다."""
    cells: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in pool:
        b = _bucket(len(r.get("text") or ""), edges)
        if b is not None and r.get("label") in GRADES:
            cells[(r["label"], b)].append(r)

    picked: list[dict] = []
    diag: list[dict] = []
    for b in range(len(edges)):
        counts = {g: len(cells[(g, b)]) for g in GRADES}
        smallest = min(counts.values())
        usable = smallest >= MIN_PER_CELL
        limit = max(smallest, int(smallest * ratio_cap)) if usable else 0
        taken = 0
        if usable:
            for g in GRADES:
                take = sorted(cells[(g, b)], key=_key)[: min(counts[g], limit)]
                picked += take
                taken += len(take)
        diag.append({
            "bucket": b,
            "range": "%d-%d" % (0 if b == 0 else edges[b - 1], edges[b]),
            "counts": counts,
            "used": usable,
            "taken": taken,
        })
    return picked, diag


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="길이 균형 홀드아웃 추출")
    ap.add_argument(
        "--train", required=True, action="append",
        help="학습셋 jsonl — 같은 원본 제거에 쓴다. **여러 번 줄 수 있다.** 모델 둘을 "
             "견주려면 양쪽 학습셋을 모두 줘야 한다 — 한쪽만 보면 다른 모델이 외운 문서가 "
             "홀드아웃에 남아 그 모델에 유리해진다(실측: v6 풀 498건 중 4건이 v5 train 에 있다).",
    )
    ap.add_argument("--pool", required=True, help="추출 풀 jsonl(쉼표로 여러 개)")
    ap.add_argument("--out", default=None, help="출력 jsonl. --dry-run 이면 무시")
    ap.add_argument("--edges", default=DEFAULT_EDGES,
                    help="길이대 경계(쉼표). 마지막 경계 이상은 쓰지 않는다. 기본 %s" % DEFAULT_EDGES)
    ap.add_argument("--ratio-cap", type=float, default=1.5,
                    help="버킷 안 등급 간 최대 배수. 1.0 이면 완전 균형, 크면 표본이 늘고 "
                         "균형이 느슨해진다. 기본 1.5")
    ap.add_argument("--dry-run", action="store_true", help="수치만 보고 파일은 쓰지 않는다")
    args = ap.parse_args(argv)

    train: list[dict] = []
    for tp in args.train:
        rows = _load(Path(tp))
        train += rows
        print("  학습셋 %s: %d행" % (tp, len(rows)))
    pool: list[dict] = []
    for p in [x.strip() for x in args.pool.split(",") if x.strip()]:
        pool += _load(Path(p))
    edges = [int(x) for x in args.edges.split(",") if x.strip()]
    print("학습 합계 %d행(%d개 셋) · 풀 %d행 · 길이대 경계 %s · 배수 <=%.1f"
          % (len(train), len(args.train), len(pool), edges, args.ratio_cap))

    picked, diag = pick(pool, edges, ratio_cap=args.ratio_cap)
    print()
    print("%-10s %6s %6s %6s %6s %8s %7s" % ("길이대", *GRADES, "사용", "추출"))
    print("-" * 54)
    for d in diag:
        print("%-10s %6d %6d %6d %6d %8s %7d"
              % (d["range"], *[d["counts"][g] for g in GRADES],
                 "예" if d["used"] else "아니오", d["taken"]))
    dropped_buckets = [d["range"] for d in diag if not d["used"]]
    if dropped_buckets:
        # 무음 절단 금지 — 버린 구간을 반드시 말한다. 그 구간의 문서는 평가되지 않는다.
        print("  ⚠ 네 등급이 다 차지 않아 버린 구간: %s — 이 길이대 문서는 평가되지 않는다"
              % " · ".join(dropped_buckets))

    index = TrainIndex(train)
    hashes = {sha(text_of(r)) for r in train}
    before = len(picked)
    picked = [r for r in picked
              if leak_reason(r, hashes, index, min_shared=3, min_ratio=0.60) is None]
    print()
    print("같은 원본 제거: %d → %d (%d건 뺌)" % (before, len(picked), before - len(picked)))

    report = assess(train, picked, label=Path(args.out).name if args.out else "balanced")
    ss = report["overlap"]["shared_sentences"]
    hl = report["holdout_leakage"]
    print()
    print("  길이-only 적중   %.3f  (임계 0.55 · 무작위 %.3f)"
          % (hl.get("length_only_1nn", 0.0), hl.get("length_only_random", 0.25)))
    print("  Theil's U        %.3f  (권고 0.25)" % hl.get("length_theils_u", 0.0))
    print("  문장 공유        %.4f  (권고 0.02) · %d종 중 상투어 %d종"
          % (ss["coverage"], ss["shared_types"], ss["boilerplate_types"]))
    print("  상투어 뺀 공유   %.4f  (%d종)" % (ss["distinctive_coverage"], ss["distinctive_types"]))
    print("  비교 사용 가능   %s" % report["usable_for_comparison"])
    for c in report["concerns"]:
        print("    ! " + c)
    print()
    print("  주장 한계 — %s" % report["claim_ceiling"])

    if args.dry_run or not args.out:
        print("\n[dry-run] 파일을 쓰지 않았다")
        return 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with io.open(out, "w", encoding="utf-8") as f:
        for r in picked:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("\n  → %s (%d행)" % (out, len(picked)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

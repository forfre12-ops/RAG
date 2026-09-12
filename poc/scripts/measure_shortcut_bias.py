"""지름길 편향을 잰다 — 본문을 읽지 않고 **부수 정보만으로** 등급이 갈리는지 본다.

왜 필요한가(2026-09-09). 설계단계 감리가 골든셋에서 "부서군과 등급의 결합"과
"다중 라벨 기준 미비"를 지적했다(감리보고서 인쇄 185쪽·186쪽). 그때 우리 쪽에서
같은 자로 재려니 재는 도구가 임시 폴더에만 있었다. 감리가 "조치 후 다시 재 보라"고
하면 재현할 수 없는 상태였다. 그래서 리포에 남긴다.

이 도구가 재는 축 세 가지 — 셋 다 **본문을 안 읽고** 맞히는 비율이다.

    부서군(domain)      학습셋에만 있다. 골든 후보 합성분에는 기록되어 있지 않다
    문서유형(제목)       골든 후보에 있다. 본문 첫 줄에 그대로 실리는 경우가 많다
    본문 길이            등급별 중앙값이 갈리면 길이만으로 등급을 추정할 수 있다

지표 둘을 함께 낸다. 하나만으로는 오해하기 쉽다.

    Cramer's V          0=무관 · 1=완전결합. 범주가 많으면 부풀 수 있다
    최빈등급 적중률       "이 축의 값만 보고 그 축에서 가장 흔한 등급을 찍는다"
                        전체 최빈등급 하나로 찍은 값과 **비교해서** 읽는다.
                        그 차이가 곧 '이 축이 추가로 알려주는 양'이다

⚠ 적중률이 높다고 곧바로 모델이 그 지름길을 쓴다는 뜻은 아니다. 이 도구가 재는 것은
   **자료에 그 신호가 있는가**이지 모델이 그것을 쓰는가가 아니다. 모델이 실제로
   반응하는지는 `measure_length_effect.py`·`measure_length_shrink.py` 처럼 입력을
   조작해서 따로 재야 한다.

사용:
    python scripts/measure_shortcut_bias.py --dataset datasets/labeled_p1_v5_clean
    python scripts/measure_shortcut_bias.py --pool datasets/proxy_gold/single_document_candidates
    python scripts/measure_shortcut_bias.py --dataset ... --pool ... --json reports/shortcut_bias.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()
GRADES = ("TS", "S1", "S2", "S3")


def _cramers_v(table: dict[str, Counter], n: int) -> float:
    """범주 x 등급 분할표의 Cramer's V. 표본이 없으면 0.0."""
    if n == 0 or not table:
        return 0.0
    col = Counter()
    for row in table.values():
        col.update(row)
    chi2 = 0.0
    for row in table.values():
        total = sum(row.values())
        for grade in GRADES:
            expected = total * col[grade] / n
            if expected > 0:
                chi2 += (row[grade] - expected) ** 2 / expected
    k = min(len(table), len(GRADES))
    if k < 2:
        return 0.0
    return (chi2 / (n * (k - 1))) ** 0.5


def _majority_hit(pairs: list[tuple[str, str]]) -> int:
    table: dict[str, Counter] = defaultdict(Counter)
    for value, grade in pairs:
        table[value][grade] += 1
    return sum(row.most_common(1)[0][1] for row in table.values())


def _null_rate(pairs: list[tuple[str, str]], *, trials: int = 200, seed: int = 7) -> float:
    """**라벨을 섞었을 때** 같은 자가 내는 적중률(%). 이것이 진짜 비교 기준이다.

    ⚠ 2026-09-12 실측: 범주가 많고 표본이 적으면 "칸마다 최빈등급 찍기"는 관계가 전혀
      없어도 크게 나온다(117건에서 40.2% · 라벨을 섞어도 39.8%). 전체 최빈등급 비율을
      기준선으로 쓰면 그 부풀림이 통째로 '지름길'로 읽힌다.
      [[permutation-baseline-not-majority-2026-09-12]]
    """
    rng = random.Random(seed)
    values = [v for v, _g in pairs]
    grades = [g for _v, g in pairs]
    total = 0
    for _ in range(trials):
        rng.shuffle(grades)
        total += _majority_hit(list(zip(values, grades)))
    return total / trials / len(pairs) * 100


def _axis_report(name: str, pairs: list[tuple[str, str]]) -> dict | None:
    """(범주값, 등급) 쌍에서 결합도와 최빈등급 적중률을 낸다."""
    pairs = [(str(v), g) for v, g in pairs if g in GRADES and v]
    n = len(pairs)
    if n == 0:
        return None
    table: dict[str, Counter] = defaultdict(Counter)
    for value, grade in pairs:
        table[value][grade] += 1
    col = Counter(g for _, g in pairs)

    # 축의 값마다 그 안의 최빈등급을 찍었을 때 맞는 건수
    hit = _majority_hit(pairs)
    # 옛 기준: 축을 안 보고 전체 최빈등급 하나로 찍는다 — **부풀림을 못 걷는다**
    baseline = col.most_common(1)[0][1]
    null = _null_rate(pairs)
    return {
        "axis": name,
        "n": n,
        "categories": len(table),
        "cramers_v": round(_cramers_v(table, n), 3),
        "majority_hit": hit,
        "majority_rate": round(hit / n * 100, 1),
        "baseline_rate": round(baseline / n * 100, 1),
        "lift_pp": round((hit - baseline) / n * 100, 1),
        "null_rate": round(null, 1),
        "excess_pp": round(hit / n * 100 - null, 1),
        "per_category": {
            value: {"n": sum(row.values()), "top": row.most_common(1)[0][0],
                    "top_share": round(row.most_common(1)[0][1] / sum(row.values()) * 100)}
            for value, row in sorted(table.items(), key=lambda kv: -sum(kv[1].values()))
        },
    }


def _length_bin_hit(pairs: list[tuple[int, str]], bins: int) -> int:
    ordered = sorted(pairs)
    hit, size = 0, len(ordered) / bins
    for i in range(bins):
        chunk = ordered[int(i * size):int((i + 1) * size)]
        if chunk:
            hit += Counter(g for _c, g in chunk).most_common(1)[0][1]
    return hit


def _length_report(lengths: dict[str, list[int]], *, bins: int = 10) -> dict | None:
    rows = {g: v for g, v in lengths.items() if g in GRADES and v}
    if not rows:
        return None
    medians = {g: int(statistics.median(v)) for g, v in rows.items()}
    lo, hi = min(medians.values()), max(medians.values())

    # 중앙값 배수만으로는 분포 겹침을 못 본다 — 길이 구간만 보고 찍는 적중률을 함께 낸다.
    # 기준선은 여기서도 **라벨 섞기**다.
    pairs = [(n, g) for g, vals in rows.items() for n in vals]
    total = len(pairs)
    real = _length_bin_hit(pairs, bins) / total * 100 if total else 0.0
    null = 0.0
    if total >= bins * 2:
        rng = random.Random(7)
        sizes = [n for n, _g in pairs]
        grades = [g for _n, g in pairs]
        acc = 0
        for _ in range(200):
            rng.shuffle(grades)
            acc += _length_bin_hit(list(zip(sizes, grades)), bins)
        null = acc / 200 / total * 100
    return {
        "counts": {g: len(v) for g, v in rows.items()},
        "median_chars": medians,
        "max_over_min": round(hi / lo, 2) if lo else None,
        "length_only_rate": round(real, 1),
        "null_rate": round(null, 1),
        "excess_pp": round(real - null, 1),
    }


def read_dataset(base: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict]:
    """학습셋 디렉터리(train/val/test.jsonl)를 읽는다."""
    domain_pairs: list[tuple[str, str]] = []
    type_pairs: list[tuple[str, str]] = []
    lengths: dict[str, list[int]] = defaultdict(list)
    for split in ("train.jsonl", "val.jsonl", "test.jsonl"):
        path = base / split
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                label = row.get("label")
                if label not in GRADES:
                    continue
                domain_pairs.append((row.get("domain") or "(없음)", label))
                doc_type = row.get("document_type") or row.get("doc_type")
                if doc_type:
                    type_pairs.append((doc_type, label))
                text = row.get("text") or row.get("content") or ""
                lengths[label].append(len(text))
    return domain_pairs, type_pairs, lengths


def _pool_body(meta: dict, pool: Path) -> str | None:
    revision = meta.get("content_revision_path")
    if revision:
        candidate = Path(revision)
        if not candidate.is_absolute():
            candidate = ROOT / revision
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    doc_id = meta.get("doc_id")
    if doc_id:
        hits = sorted(pool.glob(f"{doc_id}*.md"))
        if hits:
            return hits[0].read_text(encoding="utf-8")
    return None


def read_pool(pool: Path) -> tuple[list, list, dict, dict]:
    """골든 후보 풀(*.metadata.json)을 읽는다."""
    domain_pairs: list[tuple[str, str]] = []
    type_pairs: list[tuple[str, str]] = []
    lengths: dict[str, list[int]] = defaultdict(list)
    extra = Counter()
    for path in sorted(pool.glob("*.metadata.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))
        label = meta.get("intended_label")
        origin = meta.get("document_origin") or "(없음)"
        extra[("origin", origin)] += 1
        if label in GRADES:
            extra[("origin_label", f"{origin}/{label}")] += 1
        if meta.get("domain"):
            domain_pairs.append((meta["domain"], label))
        doc_type = meta.get("document_type")
        if doc_type:
            type_pairs.append((doc_type, label))
        body = _pool_body(meta, pool)
        if body is not None and label in GRADES:
            lengths[label].append(len(body))
            # 문서유형 문자열이 본문에 그대로 실려 있으면 그것은 본문 안의 신호다
            if doc_type and doc_type in body:
                extra[("type_in_body", "yes")] += 1
            elif doc_type:
                extra[("type_in_body", "no")] += 1
    return domain_pairs, type_pairs, lengths, extra


def _print_axis(report: dict | None, title: str) -> None:
    if report is None:
        print(f"\n[{title}] 해당 필드가 기록되어 있지 않아 재지 못했다.")
        return
    print(f"\n[{title}] 분모 {report['n']} · 범주 {report['categories']}개")
    print(f"  Cramer's V                = {report['cramers_v']}")
    print(f"  이 축만 보고 최빈등급 찍기   = {report['majority_hit']}/{report['n']}"
          f" = {report['majority_rate']}%")
    print(f"  ⭐라벨을 섞었을 때          = {report['null_rate']}%"
          f"   → **차이 {report['excess_pp']}%p** (이 값으로 판단한다)")
    print(f"  (참고) 전체 최빈등급만 찍기  = {report['baseline_rate']}%"
          f"   차이 {report['lift_pp']}%p — 범주 수에 부푼다, 판단에 쓰지 말 것")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", help="학습셋 디렉터리(train/val/test.jsonl)")
    parser.add_argument("--pool", help="골든 후보 풀 디렉터리(*.metadata.json)")
    parser.add_argument("--json", help="결과를 이 경로에 JSON 으로 남긴다")
    parser.add_argument("--top", type=int, default=0, help="축별 상위 N개 범주를 함께 출력")
    args = parser.parse_args(argv)

    if not args.dataset and not args.pool:
        parser.error("--dataset 또는 --pool 중 하나는 필요하다")

    out: dict = {}

    if args.dataset:
        base = Path(args.dataset)
        if not base.is_absolute():
            base = ROOT / args.dataset
        domain_pairs, type_pairs, lengths = read_dataset(base)
        print("=" * 72)
        print(f"학습셋: {base}")
        section = {
            "path": str(base),
            "domain": _axis_report("domain", domain_pairs),
            "document_type": _axis_report("document_type", type_pairs),
            "length": _length_report(lengths),
        }
        _print_axis(section["domain"], "부서군(domain) x 등급")
        _print_axis(section["document_type"], "문서유형 x 등급")
        if section["length"]:
            print("\n[본문 길이] 등급별 중앙값(자)")
            for grade in GRADES:
                if grade in section["length"]["median_chars"]:
                    print("  %-3s %6d건  중앙값 %6d"
                          % (grade, section["length"]["counts"][grade],
                             section["length"]["median_chars"][grade]))
            print(f"  최대/최소 배수 = {section['length']['max_over_min']}")
            print(f"  길이 구간만 보고 찍기 = {section['length']['length_only_rate']}%"
                  f" · 라벨 섞으면 {section['length']['null_rate']}%"
                  f" → **차이 {section['length']['excess_pp']}%p**")
        if args.top:
            _print_top(section, args.top)
        out["dataset"] = section

    if args.pool:
        pool = Path(args.pool)
        if not pool.is_absolute():
            pool = ROOT / args.pool
        domain_pairs, type_pairs, lengths, extra = read_pool(pool)
        print("\n" + "=" * 72)
        print(f"골든 후보 풀: {pool}")
        print("  출처별:", {k[1]: v for k, v in extra.items() if k[0] == "origin"})
        print("  출처x등급:", {k[1]: v for k, v in extra.items() if k[0] == "origin_label"})
        in_body = {k[1]: v for k, v in extra.items() if k[0] == "type_in_body"}
        section = {
            "path": str(pool),
            "domain": _axis_report("domain", domain_pairs),
            "document_type": _axis_report("document_type", type_pairs),
            "length": _length_report(lengths),
            "origin": {k[1]: v for k, v in extra.items() if k[0] == "origin"},
            "origin_label": {k[1]: v for k, v in extra.items() if k[0] == "origin_label"},
            "document_type_in_body": in_body,
        }
        _print_axis(section["domain"], "부서군(domain) x 등급")
        _print_axis(section["document_type"], "문서유형 x 등급")
        if in_body:
            total = sum(in_body.values())
            print(f"  문서유형 문자열이 본문에 그대로 있음: {in_body.get('yes', 0)}/{total}")
        if section["length"]:
            print("\n[본문 길이] 등급별 중앙값(자)")
            for grade in GRADES:
                if grade in section["length"]["median_chars"]:
                    print("  %-3s %6d건  중앙값 %6d"
                          % (grade, section["length"]["counts"][grade],
                             section["length"]["median_chars"][grade]))
            print(f"  최대/최소 배수 = {section['length']['max_over_min']}")
            print(f"  길이 구간만 보고 찍기 = {section['length']['length_only_rate']}%"
                  f" · 라벨 섞으면 {section['length']['null_rate']}%"
                  f" → **차이 {section['length']['excess_pp']}%p**")
        if args.top:
            _print_top(section, args.top)
        out["pool"] = section

    if args.json:
        target = Path(args.json)
        if not target.is_absolute():
            target = ROOT / args.json
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 기록: {target}")

    return 0


def _print_top(section: dict, top: int) -> None:
    for axis in ("domain", "document_type"):
        report = section.get(axis)
        if not report:
            continue
        print(f"\n  -- {axis} 상위 {top}개 --")
        for value, info in list(report["per_category"].items())[:top]:
            print("  %-40s %5d건  최빈 %s %d%%"
                  % (value[:40], info["n"], info["top"], info["top_share"]))


if __name__ == "__main__":
    sys.exit(main())

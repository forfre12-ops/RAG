"""합성 데이터셋 종합 분석 — 등급·도메인·길이·라벨 일치도 매트릭스.

P3 합성 산출물(datasets/synthetic/*.json) 800건을 다각도로 분석하여
발주처 보고용 리포트(Markdown + JSON) 생성.

분석 항목:
  1. 분포: 등급별·도메인별 카운트 (균등성 검증)
  2. 길이 통계: body 글자 수 / 토큰 수 추정 (등급·도메인별 평균·중앙·표준편차)
  3. 라벨 일치도: target_grade vs predicted_grade 혼동행렬
  4. 비용·시간: usage 합계 (tokens·USD·latency)
  5. PII 위반·파싱 에러 통계
  6. document_type / dept_hint 다양성

본 도구는 noop·anthropic·openai 어느 provider로 생성됐든 동일 동작.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

GRADES = ["TS", "S1", "S2", "S3"]
DOMAINS = ["tech", "business", "finance", "hr", "legal", "mixed"]


def load_corpus(synth_dir: Path) -> list[dict]:
    docs: list[dict] = []
    for f in sorted(synth_dir.glob("*.json")):
        try:
            docs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as exc:  # noqa: BLE001
            print(f"[analyze] skip {f.name}: {exc}", file=sys.stderr)
    return docs


# ─────────────────────────────────────────────────────────────
# 분석 함수들
# ─────────────────────────────────────────────────────────────


def distribution(docs: list[dict]) -> dict:
    by_grade = Counter(d.get("target_grade", "?") for d in docs)
    by_domain = Counter(d.get("domain", "?") for d in docs)
    cross = defaultdict(lambda: Counter())
    for d in docs:
        cross[d.get("target_grade", "?")][d.get("domain", "?")] += 1
    return {
        "total": len(docs),
        "by_grade": dict(by_grade),
        "by_domain": dict(by_domain),
        "by_grade_domain": {g: dict(c) for g, c in cross.items()},
    }


def length_stats(docs: list[dict]) -> dict:
    """body 글자 수 통계."""
    overall = [len(d.get("body", "")) for d in docs]
    by_grade: dict[str, list[int]] = defaultdict(list)
    by_domain: dict[str, list[int]] = defaultdict(list)
    for d in docs:
        n = len(d.get("body", ""))
        by_grade[d.get("target_grade", "?")].append(n)
        by_domain[d.get("domain", "?")].append(n)

    def _stats(xs: list[int]) -> dict:
        if not xs:
            return {"count": 0}
        return {
            "count": len(xs),
            "mean": round(statistics.mean(xs), 1),
            "median": int(statistics.median(xs)),
            "stdev": round(statistics.stdev(xs), 1) if len(xs) >= 2 else 0.0,
            "min": min(xs),
            "max": max(xs),
        }

    return {
        "overall": _stats(overall),
        "by_grade": {g: _stats(xs) for g, xs in sorted(by_grade.items())},
        "by_domain": {d: _stats(xs) for d, xs in sorted(by_domain.items())},
    }


def label_confusion(docs: list[dict]) -> dict:
    """target_grade vs predicted_grade 혼동행렬."""
    matrix: dict[str, Counter] = {g: Counter() for g in GRADES}
    matches = 0
    total = 0
    for d in docs:
        tg = d.get("target_grade")
        pg = d.get("predicted_grade")
        if tg is None or pg is None:
            continue
        total += 1
        matrix.setdefault(tg, Counter())[pg] += 1
        if d.get("label_match", tg == pg):
            matches += 1
    return {
        "total_with_prediction": total,
        "match_count": matches,
        "match_rate": round(matches / total, 4) if total else 0.0,
        "matrix_rows_target": {g: dict(c) for g, c in matrix.items()},
    }


def usage_summary(docs: list[dict]) -> dict:
    input_tok = 0
    output_tok = 0
    cost = 0.0
    latencies: list[float] = []
    providers = Counter()
    for d in docs:
        u = d.get("usage") or {}
        input_tok += int(u.get("input_tokens", 0) or 0)
        output_tok += int(u.get("output_tokens", 0) or 0)
        cost += float(u.get("cost_usd", 0.0) or 0.0)
        lat = u.get("latency_ms")
        if isinstance(lat, (int, float)):
            latencies.append(float(lat))
        providers[d.get("llm_provider", "?")] += 1
    return {
        "input_tokens_total": input_tok,
        "output_tokens_total": output_tok,
        "cost_usd_total": round(cost, 4),
        "latency_ms_avg": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "latency_ms_p95": (
            round(statistics.quantiles(latencies, n=20)[18], 2)
            if len(latencies) >= 20 else 0.0
        ),
        "by_provider": dict(providers),
    }


def diversity(docs: list[dict]) -> dict:
    doc_types = Counter(d.get("document_type", "?") for d in docs)
    depts = Counter(d.get("dept_hint", "?") for d in docs)
    pii_total = sum(len(d.get("pii_violations") or []) for d in docs)
    parse_errors = sum(1 for d in docs if d.get("parse_error"))
    return {
        "document_type_unique": len(doc_types),
        "document_type_top10": doc_types.most_common(10),
        "dept_hint_unique": len(depts),
        "dept_hint_top10": depts.most_common(10),
        "pii_violations_total": pii_total,
        "parse_errors": parse_errors,
    }


def evenness(by_grade: dict[str, int], by_domain: dict[str, int]) -> dict:
    """분포 균등성 — 표준편차/평균 비율 (CV)."""

    def _cv(xs: list[int]) -> float:
        if not xs or statistics.mean(xs) == 0:
            return 0.0
        return round(statistics.stdev(xs) / statistics.mean(xs), 4) if len(xs) >= 2 else 0.0

    return {
        "grade_cv": _cv(list(by_grade.values())),
        "domain_cv": _cv(list(by_domain.values())),
        "grade_balanced": _cv(list(by_grade.values())) < 0.1,
        "domain_balanced": _cv(list(by_domain.values())) < 0.1,
    }


# ─────────────────────────────────────────────────────────────
# 리포트 출력
# ─────────────────────────────────────────────────────────────


def format_report(analysis: dict) -> str:
    d = analysis
    md: list[str] = []
    md.append("# 합성 데이터셋 종합 분석 리포트")
    md.append("")
    md.append("- 생성: `scripts/analyze_synthetic.py`")
    md.append(f"- 코퍼스: **{d['distribution']['total']}건**")
    md.append(f"- 분포 균등성: 등급 CV={d['evenness']['grade_cv']}, 도메인 CV={d['evenness']['domain_cv']}")
    md.append(f"- 라벨 일치도: **{d['label_confusion']['match_rate']:.2%}** ({d['label_confusion']['match_count']}/{d['label_confusion']['total_with_prediction']})")
    md.append("")

    # 1. 분포
    md.append("## 1. 분포")
    md.append("")
    md.append("### 1.1 등급별")
    md.append("| 등급 | 건수 |")
    md.append("|---|---:|")
    for g in GRADES:
        md.append(f"| {g} | {d['distribution']['by_grade'].get(g, 0)} |")
    md.append("")
    md.append(f"_균등 판정: **{'PASS' if d['evenness']['grade_balanced'] else 'FAIL'}** (CV={d['evenness']['grade_cv']})_")
    md.append("")

    md.append("### 1.2 도메인별")
    md.append("| 도메인 | 건수 |")
    md.append("|---|---:|")
    for dom in DOMAINS:
        md.append(f"| {dom} | {d['distribution']['by_domain'].get(dom, 0)} |")
    md.append("")
    md.append(f"_균등 판정: **{'PASS' if d['evenness']['domain_balanced'] else 'FAIL'}** (CV={d['evenness']['domain_cv']})_")
    md.append("")

    md.append("### 1.3 등급 × 도메인 교차")
    md.append("")
    header = "| 등급 \\\\ 도메인 | " + " | ".join(DOMAINS) + " | 합계 |"
    sep = "|---|" + "|".join(["---:"] * (len(DOMAINS) + 1)) + "|"
    md.append(header)
    md.append(sep)
    for g in GRADES:
        row = d["distribution"]["by_grade_domain"].get(g, {})
        cells = [str(row.get(dom, 0)) for dom in DOMAINS]
        total = sum(row.get(dom, 0) for dom in DOMAINS)
        md.append(f"| {g} | " + " | ".join(cells) + f" | **{total}** |")
    md.append("")

    # 2. 길이
    md.append("## 2. 본문 길이 (글자 수)")
    md.append("")
    o = d["length_stats"]["overall"]
    md.append(f"전체: mean={o['mean']} / median={o['median']} / stdev={o['stdev']} / min={o['min']} / max={o['max']}")
    md.append("")
    md.append("### 2.1 등급별")
    md.append("| 등급 | 평균 | 중앙 | 표준편차 | min | max |")
    md.append("|---|---:|---:|---:|---:|---:|")
    for g in GRADES:
        s = d["length_stats"]["by_grade"].get(g, {})
        md.append(f"| {g} | {s.get('mean', '-')} | {s.get('median', '-')} | {s.get('stdev', '-')} | {s.get('min', '-')} | {s.get('max', '-')} |")
    md.append("")

    md.append("### 2.2 도메인별")
    md.append("| 도메인 | 평균 | 중앙 | 표준편차 |")
    md.append("|---|---:|---:|---:|")
    for dom in DOMAINS:
        s = d["length_stats"]["by_domain"].get(dom, {})
        md.append(f"| {dom} | {s.get('mean', '-')} | {s.get('median', '-')} | {s.get('stdev', '-')} |")
    md.append("")

    # 3. 라벨 일치도 혼동행렬
    md.append("## 3. 라벨 일치도 (target → predicted)")
    md.append("")
    md.append(f"전체 일치도: **{d['label_confusion']['match_rate']:.2%}**")
    md.append("")
    cm = d["label_confusion"]["matrix_rows_target"]
    md.append("| target \\\\ predicted | " + " | ".join(GRADES) + " | row total |")
    md.append("|---|" + "|".join(["---:"] * (len(GRADES) + 1)) + "|")
    for g in GRADES:
        row = cm.get(g, {})
        cells = [str(row.get(pg, 0)) for pg in GRADES]
        total = sum(row.values())
        md.append(f"| **{g}** | " + " | ".join(cells) + f" | {total} |")
    md.append("")

    # 4. 사용량
    md.append("## 4. LLM 사용량·비용")
    md.append("")
    u = d["usage_summary"]
    md.append(f"- input tokens: {u['input_tokens_total']:,}")
    md.append(f"- output tokens: {u['output_tokens_total']:,}")
    md.append(f"- cost (USD): ${u['cost_usd_total']:.4f}")
    md.append(f"- latency 평균: {u['latency_ms_avg']} ms / p95: {u['latency_ms_p95']} ms")
    md.append(f"- provider 분포: {u['by_provider']}")
    md.append("")

    # 5. 다양성
    md.append("## 5. 메타데이터 다양성")
    md.append("")
    div = d["diversity"]
    md.append(f"- document_type unique: **{div['document_type_unique']}**")
    md.append(f"- dept_hint unique: **{div['dept_hint_unique']}**")
    md.append(f"- PII 위반 누적: {div['pii_violations_total']}")
    md.append(f"- 파싱 에러: {div['parse_errors']}")
    md.append("")

    md.append("### 5.1 상위 document_type")
    md.append("| document_type | 건수 |")
    md.append("|---|---:|")
    for name, cnt in div["document_type_top10"]:
        md.append(f"| {name[:60]} | {cnt} |")
    md.append("")

    # 6. 합격 판정 종합
    md.append("## 6. 종합 판정")
    md.append("")
    passes = []
    fails = []
    if d["evenness"]["grade_balanced"]:
        passes.append("등급 분포 균등 (CV < 0.1)")
    else:
        fails.append(f"등급 분포 불균등 (CV={d['evenness']['grade_cv']})")
    if d["evenness"]["domain_balanced"]:
        passes.append("도메인 분포 균등 (CV < 0.1)")
    else:
        fails.append(f"도메인 분포 불균등 (CV={d['evenness']['domain_cv']})")
    if d["label_confusion"]["match_rate"] >= 0.9:
        passes.append(f"라벨 일치도 ≥ 90% ({d['label_confusion']['match_rate']:.2%})")
    else:
        fails.append(f"라벨 일치도 < 90% ({d['label_confusion']['match_rate']:.2%})")
    if div["pii_violations_total"] == 0:
        passes.append("PII 위반 0건")
    else:
        fails.append(f"PII 위반 {div['pii_violations_total']}건")
    if div["parse_errors"] == 0:
        passes.append("파싱 에러 0건")
    else:
        fails.append(f"파싱 에러 {div['parse_errors']}건")

    md.append("**통과**:")
    for p in passes:
        md.append(f"- ✅ {p}")
    if fails:
        md.append("")
        md.append("**미달**:")
        for f in fails:
            md.append(f"- ❌ {f}")
    md.append("")
    overall = "PASS" if not fails else "FAIL"
    md.append(f"**종합 판정: {overall}**")

    return "\n".join(md)


# ─────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────


def analyze(synth_dir: Path) -> dict:
    docs = load_corpus(synth_dir)
    if not docs:
        return {}
    dist = distribution(docs)
    return {
        "distribution": dist,
        "evenness": evenness(dist["by_grade"], dist["by_domain"]),
        "length_stats": length_stats(docs),
        "label_confusion": label_confusion(docs),
        "usage_summary": usage_summary(docs),
        "diversity": diversity(docs),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synth-dir", default="datasets/synthetic")
    ap.add_argument("--report", default="reports/synthetic_analysis.md")
    ap.add_argument("--json", default="reports/synthetic_analysis.json")
    args = ap.parse_args()

    synth_dir = Path(args.synth_dir)
    if not synth_dir.exists():
        print(f"[analyze] no corpus at {synth_dir}", file=sys.stderr)
        return 2

    analysis = analyze(synth_dir)
    if not analysis:
        print("[analyze] empty corpus", file=sys.stderr)
        return 2

    md_path = Path(args.report)
    json_path = Path(args.json)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(format_report(analysis), encoding="utf-8")
    json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")

    # 콘솔 요약
    d = analysis
    print(f"[analyze] total={d['distribution']['total']}")
    print(f"  grade CV={d['evenness']['grade_cv']} domain CV={d['evenness']['domain_cv']}")
    print(f"  label match={d['label_confusion']['match_rate']:.2%}")
    print(f"  doc_type unique={d['diversity']['document_type_unique']}")
    print(f"\n[analyze] reports -> {md_path}, {json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

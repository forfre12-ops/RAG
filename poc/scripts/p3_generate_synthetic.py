"""P3 PoC — LLM 기반 합성 학습 데이터 생성 + 자동 평가.

합격 기준 (doc/02 §3.3):
  - 합성문서 라벨 일치도 ≥ 90% (M3 룰 라벨러로 검증)
  - 운영 단가 추정 (provider별 cost_usd 합산)

사용:
  python scripts/p3_generate_synthetic.py --total 80 --out datasets/synthetic
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.adapters.llm import build_provider  # noqa: E402
from koipa.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator  # noqa: E402
from koipa.modules.m3_labeling import LabelingPipeline  # noqa: E402
from koipa.modules.m3_labeling.seeds import GRADE_ORDER  # noqa: E402
from koipa.services.llm_usage_service import LLMUsageService  # noqa: E402

GRADES = ["TS", "S1", "S2", "S3"]
DOMAINS = ["tech", "business", "finance", "hr", "legal", "mixed"]


def run(total: int, out_dir: Path, provider_name: str | None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    llm = build_provider(provider_name)
    gen = SyntheticDocGenerator(llm=llm)
    labeler = LabelingPipeline()
    usage_svc = LLMUsageService()

    per_grade = total // len(GRADES)
    docs: list[dict] = []
    failures = 0
    total_cost = 0.0
    total_in_tok = 0
    total_out_tok = 0
    label_hits = 0
    label_total = 0
    fnr_under = 0
    high_grade_total = 0

    # C5-4 (2026-05-30): 합성 문서 길이 다양화. 등급당 4 길이 카테고리 균등 분배.
    # 이전 500~1500 고정 → 200~10,000 범위로 확대. 실 사내 문서 분포 모사
    # (메모 / 사내보고서 / 정책서 / 정관·계약 4 카테고리).
    LENGTH_BANDS = [
        (200, 600),     # 메모·간단 통지
        (600, 1800),    # 사내 보고서 표준
        (1800, 4500),   # 정책 문서·검토 보고
        (4500, 10000),  # 계약·정관·기술 사양서
    ]

    for grade in GRADES:
        for i in range(per_grade):
            domain = DOMAINS[i % len(DOMAINS)]
            band = LENGTH_BANDS[i % len(LENGTH_BANDS)]
            req = SynthRequest(target_grade=grade, domain=domain, count=1, len_min=band[0], len_max=band[1])
            try:
                doc = gen.generate_one(req)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  [{grade}/{i}] FAIL: {exc}", file=sys.stderr)
                continue

            full_text = f"{doc.title}\n\n{doc.body}"
            label_source = "noop_fallback" if doc.parse_error == "non-json response" else "synthetic_llm"
            rec = {
                "synth_id": str(uuid4()),
                "target_grade": grade,
                "domain": domain,
                "title": doc.title,
                "body": doc.body,
                "document_type": doc.document_type,
                "dept_hint": doc.dept_hint,
                "rationale_tags": doc.rationale_tags,
                "llm_provider": doc.llm_provider,
                "label_source": label_source,
                "pii_violations": doc.pii_violations,
                "parse_error": doc.parse_error,
                "generated_at": int(time.time()),
            }

            # 비용·토큰
            if doc.usage:
                rec["usage"] = {
                    "input_tokens": doc.usage.input_tokens,
                    "output_tokens": doc.usage.output_tokens,
                    "cost_usd": doc.usage.cost_usd,
                    "latency_ms": doc.usage.latency_ms,
                }
                total_cost += doc.usage.cost_usd
                total_in_tok += doc.usage.input_tokens
                total_out_tok += doc.usage.output_tokens
                usage_svc.record(
                    doc.usage,
                    purpose="sample_generation",
                    reference_type="sample",
                    reference_id=rec["synth_id"],
                )

            # 라벨 일치 검증
            label_result = labeler.label(full_text)
            pred_grade = label_result.grade.value if hasattr(label_result.grade, "value") else str(label_result.grade)
            rec["predicted_grade"] = pred_grade
            rec["label_match"] = pred_grade == grade
            label_total += 1
            if rec["label_match"]:
                label_hits += 1
            # FNR (truth가 TS/S1인데 더 낮은 등급으로 예측)
            if GRADE_ORDER[grade] <= 2:
                high_grade_total += 1
                if GRADE_ORDER.get(pred_grade, 4) > GRADE_ORDER[grade]:
                    fnr_under += 1

            docs.append(rec)
            f = out_dir / f"{rec['synth_id']}.json"
            f.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")

    label_match_rate = label_hits / max(label_total, 1)
    fnr = fnr_under / max(high_grade_total, 1)

    summary = {
        "provider": llm.name,
        "model": llm.model,
        "total_requested": total,
        "total_generated": len(docs),
        "failures": failures,
        "by_grade": dict(Counter(d["target_grade"] for d in docs)),
        "by_domain": dict(Counter(d["domain"] for d in docs)),
        "label_match_rate": round(label_match_rate, 4),
        "fnr_underclass": round(fnr, 4),
        "cost_usd_total": round(total_cost, 6),
        "input_tokens_total": total_in_tok,
        "output_tokens_total": total_out_tok,
        "pii_violations_total": sum(1 for d in docs if d["pii_violations"]),
        "parse_errors": sum(1 for d in docs if d.get("parse_error")),
    }
    return summary


def write_report(summary: dict, out_md: Path) -> str:
    label_pass = summary["label_match_rate"] >= 0.90
    fnr_pass = summary["fnr_underclass"] <= 0.05
    verdict = "PASS" if (label_pass and fnr_pass) else "FAIL"
    md = [
        "# P3 — LLM 합성 문서 생성 리포트",
        "",
        f"- **판정**: {verdict}",
        f"- 라벨 일치도 ≥ 90%: {summary['label_match_rate']*100:.1f}% ({'PASS' if label_pass else 'FAIL'})",
        f"- FNR ≤ 5%: {summary['fnr_underclass']*100:.2f}% ({'PASS' if fnr_pass else 'FAIL'})",
        f"- Provider: `{summary['provider']}` / model: `{summary['model']}`",
        f"- 생성: {summary['total_generated']} / 요청: {summary['total_requested']} (실패 {summary['failures']})",
        f"- PII 위반: {summary['pii_violations_total']} / JSON 파싱 실패: {summary['parse_errors']}",
        f"- 비용: ${summary['cost_usd_total']:.4f} "
        f"(input {summary['input_tokens_total']:,} tok, output {summary['output_tokens_total']:,} tok)",
        f"- 등급별: {summary['by_grade']}",
        f"- 도메인별: {summary['by_domain']}",
    ]
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(md), encoding="utf-8")
    out_md.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=40, help="총 생성 건수 (4 등급에 균등 분배)")
    ap.add_argument("--out", default="datasets/synthetic/draft",
                    help="생성 결과 저장 경로 (검수 전 draft). 검수 후 synthetic/accepted/로 이동.")
    ap.add_argument("--provider", default=None, help="noop|anthropic|openai|vllm (생략 시 settings)")
    ap.add_argument("--report", default="reports/p3_synthesis_report.md")
    args = ap.parse_args()

    summary = run(args.total, Path(args.out), args.provider)
    verdict = write_report(summary, Path(args.report))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n[p3] verdict: {verdict} -> {args.report}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

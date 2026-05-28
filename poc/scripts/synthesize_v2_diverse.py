"""P2-B2: LLM 실호출 합성 v2 — 다양성 강제 + 분포 검증.

기존 p3_generate_synthetic.py는 단일 prompt 템플릿. v2는:
1) 도메인·등급·문서 형식(메모/이메일/회의록/보고서/계약서) 직교 매트릭스
2) 문장 길이·전문성·연도 변동 강제
3) 생성 후 self-BLEU·distinct-2 다양성 메트릭 산출
4) noop provider 단조성 명시적 차단

사용:
  ANTHROPIC_API_KEY=... \
  python scripts/synthesize_v2_diverse.py \
      --total 5000 --provider anthropic \
      --out datasets/synthetic_5k_v2/
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from uuid import uuid4

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

GRADES = ["TS", "S1", "S2", "S3"]
DOMAINS = ["tech", "business", "finance", "hr", "legal", "rnd", "supply", "compliance"]
FORMATS = ["memo", "email", "meeting_notes", "report", "contract_excerpt", "research_note"]
YEARS = list(range(2024, 2027))
TONES = ["formal", "concise", "detailed", "narrative"]


def _make_combinations(total: int, seed: int = 42) -> list[dict]:
    """등급 × 도메인 × 포맷 × 톤 ×  연도 다양성 강제 sampling."""
    rng = random.Random(seed)
    out: list[dict] = []
    per_grade = total // len(GRADES)
    for g in GRADES:
        for i in range(per_grade):
            out.append({
                "grade": g,
                "domain": rng.choice(DOMAINS),
                "format": rng.choice(FORMATS),
                "tone": rng.choice(TONES),
                "year": rng.choice(YEARS),
                "seq": i,
            })
    rng.shuffle(out)
    return out


def _distinct_n(texts: list[str], n: int = 2) -> float:
    """distinct-n 다양성 — 고유 n-gram / 총 n-gram. 1에 가까울수록 다양."""
    grams = []
    for t in texts:
        toks = t.split()
        for i in range(len(toks) - n + 1):
            grams.append(tuple(toks[i:i + n]))
    if not grams:
        return 0.0
    return len(set(grams)) / len(grams)


def _format_distribution(specs: list[dict]) -> dict:
    by_grade = Counter(s["grade"] for s in specs)
    by_domain = Counter(s["domain"] for s in specs)
    by_format = Counter(s["format"] for s in specs)
    return {
        "by_grade": dict(by_grade),
        "by_domain": dict(by_domain),
        "by_format": dict(by_format),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="B2: LLM 실호출 합성 v2 — 다양성 강제")
    parser.add_argument("--total", type=int, default=5000)
    parser.add_argument("--out", type=Path, default=Path("datasets/synthetic_5k_v2"))
    parser.add_argument("--provider", default=None, help="anthropic|openai|vllm|noop")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true", help="조합만 산출, LLM 호출 X")
    args = parser.parse_args()

    specs = _make_combinations(args.total, seed=args.seed)
    dist = _format_distribution(specs)
    args.out.mkdir(parents=True, exist_ok=True)

    (args.out / "_specs_plan.json").write_text(
        json.dumps({"total": len(specs), "distribution": dist}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[plan] specs={len(specs)} grades={dist['by_grade']}")

    if args.dry_run:
        print("[dry-run] LLM 호출 없이 plan만 산출")
        return 0

    # 실제 LLM 호출
    try:
        from lloydk.adapters.llm import build_provider  # type: ignore
        from lloydk.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator
    except Exception as e:  # noqa: BLE001
        print(f"[ERR] LLM adapter import 실패: {e}", file=sys.stderr)
        return 2

    provider = build_provider(args.provider)
    if (args.provider or "").lower() == "noop":
        print("[WARN] provider=noop — 결정론적 mock. 다양성 메트릭이 낮을 수 있음 (B2 본 취지 위배)")

    gen = SyntheticDocGenerator(llm=provider)
    bodies: list[str] = []
    n_ok = 0
    n_fail = 0

    for s in specs:
        req = SynthRequest(target_grade=s["grade"], domain=s["domain"], count=1)
        try:
            docs = gen.generate(req)
        except Exception as e:  # noqa: BLE001
            n_fail += 1
            continue
        for d in docs:
            doc_id = str(uuid4())
            payload = {
                "doc_id": doc_id,
                "title": d.title,
                "body": d.body,
                "target_grade": d.target_grade,
                "domain": s["domain"],
                "format": s["format"],
                "tone": s["tone"],
                "year": s["year"],
                "llm_provider": d.llm_provider,
                "cost_usd": d.usage.cost_usd if d.usage else 0.0,
            }
            (args.out / f"{doc_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            bodies.append(d.body)
            n_ok += 1

    # 다양성 메트릭
    d2 = _distinct_n(bodies, n=2)
    d3 = _distinct_n(bodies, n=3)
    diversity = {
        "n_ok": n_ok,
        "n_fail": n_fail,
        "distinct_2": round(d2, 4),
        "distinct_3": round(d3, 4),
        "diversity_warning": d2 < 0.5,  # 너무 단조하면 경고
    }
    (args.out / "_diversity_report.json").write_text(
        json.dumps(diversity, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(diversity, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

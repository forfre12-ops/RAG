# -*- coding: utf-8 -*-
"""다단계 생성(개요→본문→검토→수정)이 단발 생성과 무엇이 다른가 — 같은 자로 잰다.

무엇을 재는가
  같은 (등급, 도메인) 조합을 두 방식으로 만들어 다음을 센다.
    · LLM 호출 수 · 소요 시간              — 비용이 얼마나 느는가
    · 본문 글자수 · 줄 수 · 절 제목 수      — 구조가 실제로 생기는가
    · 자체검토 지적 수 · 재작성 발동 여부    — 검토가 일을 하는가
    · 등급명 노출 · PII 위반                — 품질 게이트가 보는 항목이 나빠지지 않는가

무엇을 재지 못하는가
  ⛔ **실문서 분류 성능이 오르는지는 여기서 알 수 없다.** 그것은 학습·평가를 거쳐야
     나오는 값이고, 합성-only 학습의 실문서 F1 은 0.26 으로 이미 막혀 있다.
     이 자는 "문서가 문서답게 나오는가"까지만 말한다.

쓰기
  python scripts/measure_multistep_effect.py --model qwen3:14b --n 5
"""
from __future__ import annotations

# 콘솔 출구를 UTF-8 로 고정한다 — cp949 콘솔에서 em dash 하나에 죽던 것을 막는다.
try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from koipa.adapters.llm.local_openai_provider import LocalOpenAIProvider  # noqa: E402
from koipa.modules.m1_synthesis.generator import (  # noqa: E402
    FORBIDDEN_GRADE_TERMS,
    SynthRequest,
    SyntheticDocGenerator,
)

CASES = [
    ("S1", "반도체"),
    ("TS", "security"),
    ("S2", "finance"),
    ("S3", "public"),
    ("S1", "배터리"),
    ("S2", "legal"),
    ("TS", "ma"),
    ("S3", "hr"),
]

# 절 제목처럼 보이는 줄 — "1. 개요" · "## 개요" · "[개요]"
_HEADING = re.compile(r"^\s*(?:#{1,4}\s+\S|\d+[.)]\s+\S|\[[^\]]{2,20}\]\s*$)", re.M)


def _grade_terms(text: str) -> list[str]:
    return sorted({t for t in FORBIDDEN_GRADE_TERMS if t in text})


def _measure(gen, grade, domain, len_min, len_max, max_tokens) -> dict:
    start = time.perf_counter()
    doc = gen.generate_one(
        SynthRequest(
            target_grade=grade,
            domain=domain,
            len_min=len_min,
            len_max=len_max,
            max_output_tokens=max_tokens,
        )
    )
    elapsed = time.perf_counter() - start
    steps = [str(a["step"]) for a in doc.response_audit]
    return {
        "grade": grade,
        "domain": domain,
        "mode": doc.generation_mode,
        "seconds": round(elapsed, 1),
        "calls": len(doc.response_audit),
        "steps": steps,
        "parse_error": doc.parse_error,
        "body_chars": len(doc.body or ""),
        "body_lines": (doc.body or "").count("\n") + 1,
        "headings": len(_HEADING.findall(doc.body or "")),
        "critique_issues": len(doc.critique_issues),
        "revised": "revise" in steps,
        "grade_terms": _grade_terms((doc.title or "") + "\n" + (doc.body or "")),
        "pii": doc.pii_violations,
    }


def _summary(rows: list[dict]) -> dict:
    def med(key):
        vals = [r[key] for r in rows]
        return round(statistics.median(vals), 1) if vals else None

    return {
        "n": len(rows),
        "median_seconds": med("seconds"),
        "median_calls": med("calls"),
        "median_body_chars": med("body_chars"),
        "median_body_lines": med("body_lines"),
        "median_headings": med("headings"),
        "parse_error": sum(1 for r in rows if r["parse_error"]),
        "grade_leak_docs": sum(1 for r in rows if r["grade_terms"]),
        "pii_docs": sum(1 for r in rows if r["pii"]),
        "revised": sum(1 for r in rows if r["revised"]),
        "total_issues": sum(r["critique_issues"] for r in rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:14b")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--api-key", default="ollama")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--len-min", type=int, default=600)
    ap.add_argument("--len-max", type=int, default=1200)
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    llm = LocalOpenAIProvider(
        base_url=args.base_url, model=args.model,
        api_key=args.api_key, provider_label="ollama",
    )
    cases = (CASES * ((args.n // len(CASES)) + 1))[: args.n]

    rows: dict[str, list[dict]] = {}
    for label, multi in (("single", False), ("multi_step", True)):
        gen = SyntheticDocGenerator(llm=llm, multi_step=multi)
        print("── %s ──" % label, flush=True)
        collected = []
        for grade, domain in cases:
            row = _measure(gen, grade, domain, args.len_min, args.len_max, args.max_tokens)
            collected.append(row)
            print(
                "  %-3s %-10s %5.1f초 · 호출%d · %4d자 · 절%d · 지적%d%s"
                % (
                    grade, domain, row["seconds"], row["calls"], row["body_chars"],
                    row["headings"], row["critique_issues"],
                    " · 등급노출%s" % row["grade_terms"] if row["grade_terms"] else "",
                ),
                flush=True,
            )
        rows[label] = collected

    result = {
        "model": args.model,
        "n_per_mode": args.n,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "single": _summary(rows["single"]),
        "multi_step": _summary(rows["multi_step"]),
        "rows": rows,
    }
    a, b = result["single"], result["multi_step"]
    print("")
    print("=" * 70)
    print(" %-16s %12s %12s" % ("", "단발", "다단계"))
    for key, name in (
        ("median_calls", "중앙 호출수"),
        ("median_seconds", "중앙 소요(초)"),
        ("median_body_chars", "중앙 본문(자)"),
        ("median_body_lines", "중앙 줄수"),
        ("median_headings", "중앙 절제목수"),
        ("parse_error", "파싱실패 문서"),
        ("grade_leak_docs", "등급명 노출 문서"),
        ("pii_docs", "PII 위반 문서"),
    ):
        print(" %-16s %12s %12s" % (name, a[key], b[key]))
    print(" %-16s %12s %12s" % ("검토 지적 합", "-", b["total_issues"]))
    print(" %-16s %12s %12s" % ("재작성 발동", "-", b["revised"]))
    print("=" * 70)

    out = args.out or "reports/multistep_effect_%s.json" % args.model.replace(":", "_").replace("/", "_")
    path = _ROOT / out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("기록: %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

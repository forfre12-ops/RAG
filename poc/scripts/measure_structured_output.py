# -*- coding: utf-8 -*-
"""구조화 출력이 JSON 파싱 실패를 실제로 줄이는가 — 같은 자로 두 번 잰다.

무엇을 재는가
  같은 프롬프트·같은 모델로 문서 N건을 두 조건에서 만든다.
    A(스키마 없음)  종전 방식. 프롬프트로 "JSON 만 출력하라"고 부탁만 한다.
    B(스키마 있음)  서버에 response_format=json_schema 를 넘긴다.
  세는 것은 **1회차 파싱 실패 건수**다. 실패하면 재시도 호출이 붙으므로 그만큼
  호출 수·시간·비용이 는다.

왜 이 자가 필요한가
  "구조화 출력을 켰다"는 말과 "실패가 줄었다"는 말은 다르다. 서버가 인자를 무시해도
  겉으로는 똑같이 문서가 나온다. 재보지 않으면 켠 줄 알고 안 켜진 상태로 지낸다.

쓰기
  python scripts/measure_structured_output.py --model exaone3.5:2.4b --n 20
  python scripts/measure_structured_output.py --model qwen3:14b --n 10 --out reports/so_qwen.json

  --base-url 로 다른 OpenAI 호환 서버(vLLM 등)를 가리킬 수 있다.
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
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from koipa.adapters.llm.local_openai_provider import LocalOpenAIProvider  # noqa: E402
from koipa.modules.m1_synthesis.generator import (  # noqa: E402
    SYNTH_DOC_JSON_SCHEMA,
    SYSTEM_PROMPT,
    SynthRequest,
    SyntheticDocGenerator,
)

GRADES = ("TS", "S1", "S2", "S3")
DOMAINS = ("tech", "finance", "hr", "legal", "public", "반도체", "배터리")


def _run_arm(gen, llm, *, schema, n, len_min, len_max, max_tokens):
    """한 조건으로 n 건 생성하고 1회차 결과만 센다(재시도는 부르지 않는다)."""
    rows = []
    for i in range(n):
        grade = GRADES[i % len(GRADES)]
        domain = DOMAINS[i % len(DOMAINS)]
        req = SynthRequest(
            target_grade=grade,
            domain=domain,
            len_min=len_min,
            len_max=len_max,
            max_output_tokens=max_tokens,
        )
        user = gen._build_user_prompt(req, grade, domain)
        start = time.perf_counter()
        resp = llm.generate(
            user,
            system=SYSTEM_PROMPT,
            temperature=0.7,
            max_tokens=max_tokens,
            **({"json_schema": schema} if schema else {}),
        )
        elapsed = time.perf_counter() - start
        parsed = gen._parse(resp.text)
        body = (parsed or {}).get("body", "") if isinstance(parsed, dict) else ""
        rows.append(
            {
                "i": i,
                "grade": grade,
                "domain": domain,
                "parse_ok": parsed is not None,
                "call_ok": bool(resp.usage.success),
                "error_code": resp.usage.error_code,
                "seconds": round(elapsed, 2),
                "output_chars": len(resp.text or ""),
                "body_chars": len(body or ""),
                "finish_reason": resp.meta.get("finish_reason"),
            }
        )
        mark = "o" if parsed is not None else "X"
        print(
            "  [%2d/%2d] %s %-10s %-12s %5.1f초 %5d자"
            % (i + 1, n, mark, grade, domain, elapsed, len(resp.text or "")),
            flush=True,
        )
    return rows


def _summarize(rows):
    n = len(rows)
    fails = [r for r in rows if not r["parse_ok"]]
    secs = [r["seconds"] for r in rows]
    bodies = [r["body_chars"] for r in rows if r["parse_ok"]]
    return {
        "n": n,
        "parse_fail": len(fails),
        "parse_fail_rate": round(len(fails) / n, 4) if n else None,
        "median_seconds": round(statistics.median(secs), 2) if secs else None,
        "median_body_chars": int(statistics.median(bodies)) if bodies else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:14b")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--api-key", default="ollama")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--len-min", type=int, default=400)
    ap.add_argument("--len-max", type=int, default=900)
    ap.add_argument("--max-tokens", type=int, default=1400)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    llm = LocalOpenAIProvider(
        base_url=args.base_url,
        model=args.model,
        api_key=args.api_key,
        provider_label="ollama",
    )
    gen = SyntheticDocGenerator(llm=llm, structured_output=True)
    if not gen.structured_output:
        print("이 provider 는 구조화 출력을 지원하지 않는다 — 잴 것이 없다.")
        return 2

    print("모델 %s · %s · %d건씩" % (args.model, args.base_url, args.n))
    print("A) 스키마 없음 — 종전 방식")
    arm_a = _run_arm(
        gen, llm, schema=None, n=args.n,
        len_min=args.len_min, len_max=args.len_max, max_tokens=args.max_tokens,
    )
    print("B) 스키마 있음 — response_format=json_schema")
    arm_b = _run_arm(
        gen, llm, schema=SYNTH_DOC_JSON_SCHEMA, n=args.n,
        len_min=args.len_min, len_max=args.len_max, max_tokens=args.max_tokens,
    )

    sum_a, sum_b = _summarize(arm_a), _summarize(arm_b)
    result = {
        "model": args.model,
        "base_url": args.base_url,
        "n_per_arm": args.n,
        "len_min": args.len_min,
        "len_max": args.len_max,
        "max_output_tokens": args.max_tokens,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "no_schema": sum_a,
        "with_schema": sum_b,
        "rows": {"no_schema": arm_a, "with_schema": arm_b},
    }
    print("")
    print("=" * 66)
    print(" %-14s %10s %10s" % ("", "스키마 없음", "스키마 있음"))
    print(" %-14s %10s %10s" % ("1회차 파싱실패", "%d/%d" % (sum_a["parse_fail"], sum_a["n"]),
                                 "%d/%d" % (sum_b["parse_fail"], sum_b["n"])))
    print(" %-14s %10s %10s" % ("중앙 소요(초)", sum_a["median_seconds"], sum_b["median_seconds"]))
    print(" %-14s %10s %10s" % ("중앙 본문(자)", sum_a["median_body_chars"], sum_b["median_body_chars"]))
    print("=" * 66)

    out = args.out or "reports/structured_output_%s.json" % args.model.replace(":", "_").replace("/", "_")
    path = _ROOT / out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("기록: %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

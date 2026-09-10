#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""앵커의 '필수 사실 토큰'이 정말 본문의 사실인지 전수로 검사한다.

결정적 시험(2026-09-10): **앵커 자기 본문**을 자기 required_tokens 로 사실보존 게이트에
넣는다. 원본이 자기 검사를 통과하지 못하면 그 토큰은 본문에 없는 값이다.

⚠ **그것을 결함으로 읽지 말 것.** 앵커의 89%(1,189/1,231)는 NKT 특허(source=anchor_nkt)이고,
  등급 근거가 본문이 아니라 **KIPO 공식 소분류 라벨**이다(grade_basis=nkt). 특허는
  "비밀을 만든 한 문장"을 분리할 수 없다 — 기술 개시 전체가 비밀이다. anchor_corpus 모듈이
  그 한계를 이미 적어 두었다("NKT 특허 추상은 수치 구체값이 드물어 거의 안 잡힌다 …
  해당 앵커는 reverse 부적격 유지"). 그러므로 이 앵커들에 사람이 본문 문장을 채워 넣는
  것은 **고칠 일이 아니라 틀린 일**이다.

이 검사기가 내는 값은 "메타모픽 역방향 시험이 의미 있게 도는 앵커가 몇 건인가"이다.
분모는 1,231 이 아니라 **holdout_gold 계열**이다.

왜 만들었는가: 2026-09-10 에 앵커 60건으로 생성했더니 사실보존 게이트 채택이 12건(20%)
뿐이었다. Qwen 생성 품질 문제로 보였으나 아니었고, 앵커 결함도 아니었다 — 앵커 종류가
그 시험에 안 맞는 것이었다.

사용:
    python scripts/audit_anchor_required_tokens.py
    python scripts/audit_anchor_required_tokens.py --json reports/anchor_token_audit.json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# cp949 콘솔에서 em dash 하나에 죽는 것을 막는다 — 문자를 바꾸지 말고 출구를 고정한다.
# 정본은 scripts/_cli_io.py 한 곳이다(tests/test_scripts_console_encoding.py 가 강제).
try:  # 스크립트로 직접 실행
    from _cli_io import force_utf8_stdio
except ImportError:  # 패키지로 import
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="", help="결과를 JSON 으로도 저장할 경로")
    ap.add_argument("--grades", default="TS,S1", help="검사할 앵커 등급")
    a = ap.parse_args()

    from koipa.modules.m6_evaluation.anchor_corpus import load_anchor_corpus
    from koipa.modules.m6_evaluation.metamorphic import check_fact_preservation

    grades = tuple(g.strip() for g in a.grades.split(",") if g.strip())
    recs = load_anchor_corpus()
    total = len(recs)
    hi = [r for r in recs if r.anchor_grade in grades and r.required_tokens]
    no_tok = [r for r in recs if r.anchor_grade in grades and not r.required_tokens]

    by_source: collections.Counter = collections.Counter(
        str(getattr(r, "source", "?")) for r in recs)
    print("앵커 출처: %s" % dict(by_source.most_common()))
    print("  anchor_nkt = NKT 특허. 등급 근거가 본문이 아니라 공식 분류 라벨이라")
    print("  역방향(사실 제거) 시험 대상이 아니다. 아래 '탈락'에 그대로 잡힌다.")
    print()

    usable, broken = [], []
    reasons: collections.Counter = collections.Counter()
    for r in hi:
        res = check_fact_preservation(r.text, r.required_tokens)
        if res.admit:
            usable.append(r)
        else:
            broken.append((r, res))
            reasons["negated" if res.negated_tokens else "missing"] += 1

    n = len(hi) or 1
    print("앵커 전체 %d건 · 검사 대상(%s · 토큰 보유) %d건 · 토큰 없음 %d건"
          % (total, "/".join(grades), len(hi), len(no_tok)))
    print()
    print("자기 본문을 자기 필수토큰으로 검사한 결과")
    print("  통과 — 하니스가 돌 수 있는 앵커   %5d  %5.1f%%" % (len(usable), len(usable) / n * 100))
    print("  탈락 — 토큰이 본문에 없는 앵커     %5d  %5.1f%%  %s"
          % (len(broken), len(broken) / n * 100, dict(reasons)))
    print()
    nkt_broken = sum(1 for r, _ in broken if str(getattr(r, "source", "")) == "anchor_nkt")
    print("  탈락 %d건 중 %d건이 NKT 특허다 — 고칠 대상이 아니라 시험 대상이 아닌 것이다."
          % (len(broken), nkt_broken))
    print("  나머지 %d건만 사람이 본문에서 결정적 대목을 채울 여지가 있다."
          % (len(broken) - nkt_broken))

    tokcnt = collections.Counter(t for r, _ in broken for t in r.required_tokens)
    print()
    print("  탈락 앵커에서 가장 흔한 토큰 (상위 10):")
    for t, c in tokcnt.most_common(10):
        print("    %5d  %s" % (c, t))

    if a.json:
        out = Path(a.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "anchors_total": total, "checked": len(hi), "no_tokens": len(no_tok),
            "usable": len(usable), "broken": len(broken),
            "broken_reasons": dict(reasons),
            "top_broken_tokens": tokcnt.most_common(30),
            "usable_anchor_ids": [r.anchor_id for r in usable],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n[done] %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

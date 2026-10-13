#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""회원사 정책표를 검사하고, 사실을 주면 **등급 후보**를 뽑아 본다.

정책표는 규정 원문이 아니라 담당자가 채우는 입력물이다 — 규정만으로는 못 채운다는 것을
공개 규정 3건으로 확인했다(대조에 필요한 두 열 0/11).

⚠ 받은 표를 그대로 믿지 않는다. 사실 이름 오타 하나가 그 조건을 **조용히 무시**시키고,
  등급 순서가 없으면 "증거 없을 때 하향 금지"가 아예 성립하지 않는다.

사용:
    python scripts/validate_org_policy.py datasets/mapping_tables/POLICY_TEMPLATE.json
    python scripts/validate_org_policy.py <파일> --facts '{"public_disclosed": false, "access_scope": "approved_only"}'
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))

from koipa.modules.m3_labeling.policy_engine import (  # noqa: E402
    EXTRACTED_FACTS,
    SUPPLIED_FACTS,
    evaluate,
    load,
    validate,
)


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="회원사 정책표 검사·시연")
    ap.add_argument("path")
    ap.add_argument("--facts", default="", help="사실 JSON — 주면 등급 후보를 계산한다")
    a = ap.parse_args(argv)

    policy = load(a.path)
    print("회원사 %s · 정책 %s · 시행일 %s"
          % (policy.org_id or "(비어 있음)", policy.version or "(비어 있음)",
             policy.effective_date or "(비어 있음)"))
    print("등급 순서(민감→낮음): %s" % (" > ".join(policy.grade_order) or "(없음)"))
    print("규칙 %d건 · 기본 등급 %s" % (len(policy.rules), policy.default_grade or "(없음)"))

    issues = validate(policy)
    if issues:
        print("\n판정: 아직 못 씁니다 — %d건" % len(issues))
        for issue in issues:
            print("  · %s" % issue)
    else:
        print("\n판정: 쓸 수 있습니다.")

    if a.facts:
        facts = json.loads(a.facts)
        unknown = [k for k in facts if k not in EXTRACTED_FACTS + SUPPLIED_FACTS]
        if unknown:
            print("\n⚠ 모르는 사실 이름: %s — 그 값은 무시됩니다" % ", ".join(unknown))
        out = evaluate(policy, facts)
        print("\n등급 후보: %s" % (out.grade or "(없음)"))
        print("  적용 규칙 : %s" % (out.rule_id or "(없음)"))
        print("  충족 조건 : %s" % (" · ".join(out.matched) or "(없음)"))
        if out.missing_evidence:
            print("  빠진 증거 : %s" % " · ".join(out.missing_evidence))
        if out.blocked_rules:
            print("  보류 규칙 : %s" % " · ".join(out.blocked_rules))
        print("  검수 필요 : %s" % ("예" if out.needs_review else "아니오"))
        print("  사유      : %s" % out.reason)
        print("\n⛔ 이것은 **후보**입니다. 확정은 담당자가 합니다.")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())

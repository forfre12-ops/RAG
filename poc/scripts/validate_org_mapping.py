#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""회원사 등급 매핑표를 검사한다 — 채워져 왔을 때 바로 쓸 수 있는지 본다.

매핑표는 규정에서 뽑는 것이 아니라 담당자가 채우는 입력물이다(2026-09-12 실측: 공개 규정
3건으로 시험했더니 대조에 필요한 두 열이 0/11 이었다). 그래서 **받은 표를 그대로 믿지 않고**
우리 어휘로 검사한다. 어휘 밖의 값이 섞이면 조용히 무시되어 그 문서는 관리성(M)을 영영
못 받는다 — 그러면 1급비밀·특급기밀이 구조적으로 도달 불가가 된다.

사용:
    python scripts/validate_org_mapping.py datasets/mapping_tables/TEMPLATE.json
    python scripts/validate_org_mapping.py <파일> --sample 대외비 부서한정
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))

from koipa.modules.m3_labeling.org_mapping import (  # noqa: E402
    coverage,
    load,
    validate,
)


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="회원사 등급 매핑표 검사")
    ap.add_argument("path")
    ap.add_argument("--sample", nargs=2, metavar=("표기", "열람범위"),
                    help="이 두 값을 실제로 옮겨 본다")
    a = ap.parse_args(argv)

    mapping = load(a.path)
    cov = coverage(mapping)
    print("회원사: %s" % (mapping.org_id or "(비어 있음)"))
    print("근거 규정: %s" % (mapping.source or "(비어 있음)"))
    print("채움: 필수 %d칸 중 %d칸 · 표기 %d줄 · 열람범위 %d줄 · 문서종류 %d등급"
          % (cov["sections_required"], cov["sections_filled"], cov["markings"],
             cov["scopes"], cov["document_type_grades"]))

    issues = validate(mapping)
    if not issues:
        print("\n판정: 쓸 수 있습니다.")
    else:
        print("\n판정: 아직 못 씁니다 — %d건" % len(issues))
        for issue in issues:
            where = f"{issue.section}" + (f"[{issue.key}]" if issue.key else "")
            print("  · %-22s %s" % (where, issue.message))

    if a.sample:
        raw_marking, raw_scope = a.sample
        out = mapping.translate(raw_marking, raw_scope)
        print("\n옮겨 보기: 표기 '%s' · 열람범위 '%s'" % (raw_marking, raw_scope))
        print("  security_marking = %s" % (out["security_marking"] or "(못 옮김)"))
        print("  access_scope     = %s" % (out["access_scope"] or "(못 옮김)"))
        if out["unmapped"]:
            print("  ⚠ 표에 없는 값: %s — 이 문서는 관리성(M)을 못 받습니다"
                  % ", ".join(out["unmapped"]))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())

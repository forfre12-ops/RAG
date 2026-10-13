"""config.py의 bool 피처 플래그를 전수 집계한다 — 기본 꺼짐(opt-in)이 몇 개인지,
어떤 이름인지 셀 때마다 손으로 grep 하지 않도록(2026-10-03, 제3자 검토서 "23개 중 14개
꺼짐" 수치 재검증 목적).

사용: python scripts/audit_optin_features.py
"""
from __future__ import annotations

import re
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "src" / "koipa" / "config.py"
# 이름으로 "opt-in 성격 기능 플래그"로 볼 것만 추린다 — NFR-SEC-01 비밀키처럼 보안값이거나
# compute_* 같은 계산 필드, 순수 숫자/문자열 설정은 "기능 on/off"가 아니라 빼야 더 정확하다.
_FIELD_RE = re.compile(r'^\s{4}(\w+)\s*:\s*bool\s*=\s*(True|False)\s*(?:#.*)?$')


def main() -> int:
    text = CONFIG.read_text(encoding="utf-8")
    lines = text.splitlines()
    fields: list[tuple[str, bool, int]] = []
    for i, line in enumerate(lines, start=1):
        m = _FIELD_RE.match(line)
        if m:
            fields.append((m.group(1), m.group(2) == "True", i))

    on = [f for f in fields if f[1]]
    off = [f for f in fields if not f[1]]

    print(f"config.py의 bool 필드 전체: {len(fields)}개 (줄 {fields[0][2]}~{fields[-1][2]})")
    print(f"  기본 True(켜짐):  {len(on)}개")
    print(f"  기본 False(꺼짐·opt-in): {len(off)}개")
    print()
    print("기본 False(opt-in) 목록:")
    for name, _, ln in off:
        print(f"  {ln:>5}  {name}")
    print()
    print("기본 True(켜짐) 목록:")
    for name, _, ln in on:
        print(f"  {ln:>5}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""보존기간이 지난 운영 로그를 지운다 — 켜기 전에 --dry-run 으로 먼저 볼 것.

    python scripts/purge_retention.py --dry-run     # 몇 행이 지워질지만 센다
    python scripts/purge_retention.py               # 실제로 지운다

기본값은 설정(config.py retention_*)에서 읽는다. `retention_enabled=False` 면
--dry-run 도 'disabled' 로 끝난다 — 그때는 --force-enabled 로 설정을 무시하고 센다.

⚠ 감사로그는 컴플라이언스 기록(NFR-SEC-01)이다. 실행 전에 보존기간이 발주처 정책과
  맞는지 확인할 것. 지운 것은 되돌릴 수 없다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="운영 로그 보존기간 삭제")
    ap.add_argument("--dry-run", action="store_true", help="세기만 하고 지우지 않는다")
    ap.add_argument(
        "--force-enabled",
        action="store_true",
        help="retention_enabled=False 여도 진행한다(--dry-run 과 함께 쓰길 권한다)",
    )
    ap.add_argument(
        "--max-slices", type=int, default=120, help="한 번에 처리할 월 슬라이스 상한"
    )
    a = ap.parse_args(argv)

    from koipa.config import settings
    from koipa.services.retention import RETAINED_TABLES, purge_expired

    if a.force_enabled:
        settings.retention_enabled = True

    print("보존기간 설정")
    for table, col, key in RETAINED_TABLES:
        days = int(getattr(settings, key, 0) or 0)
        print(f"  {table:22s} {col:14s} {days:5d}일" + ("  (무제한 — 삭제 안 함)" if days <= 0 else ""))
    print(f"  retention_enabled = {settings.retention_enabled}")
    print()

    out = purge_expired(max_slices=a.max_slices, dry_run=a.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=2))

    if out["status"] == "disabled":
        print("\n설정이 꺼져 있어 아무것도 하지 않았다 — --force-enabled 로 세어볼 수 있다.")
    elif a.dry_run:
        print("\n--dry-run 이라 지우지 않았다. 위 건수가 실제 삭제 대상이다.")
    return 0 if out["status"] in ("ok", "disabled") else 1


if __name__ == "__main__":
    raise SystemExit(main())

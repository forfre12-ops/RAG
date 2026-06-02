"""운영망 Alembic 리비전 드리프트 점검 — 배포된 DB가 expected head까지 올라갔는가.

배경(2026-06-02): 기존 ci_alembic_drift_check.sh는 CI에서 fresh DB로 "리비전 작성을
빼먹었나"(model↔migration 동기화)를 본다. 본 스크립트는 운영망에서 "배포된 DB가
expected head까지 적용됐나"(applied↔head)를 본다 — 둘은 시점·대상이 다르다.

verify_infra.check_postgres는 baseline 4개 테이블 존재만 확인하므로, 이후 마이그레이션
(guides 테이블 등)이 미적용이어도 GREEN을 줘 silent drift가 점검을 통과한다. 본 검사는
그 사각지대를 닫는다. doc/12 §9.2(월 1회 upgrade)·doc/23 §8.3(폐쇄망 설치) 직후 호출하거나
일일 운영 점검 cron에 둔다.

in-process 비교(외부 alembic CLI 파싱 회피): 프로젝트 alembic/env.py가 settings.database_url과
Base.metadata를 쓰는 패턴을 그대로 재사용한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def compare_heads(db_heads, script_heads) -> dict:
    """적용 리비전 집합 vs 기대 head 집합 비교(순수 함수 — 단위 테스트 가능)."""
    db = set(db_heads or [])
    expected = set(script_heads or [])
    missing = sorted(expected - db)   # 적용돼야 하는데 안 된 것(=upgrade 필요)
    unknown = sorted(db - expected)   # DB엔 있는데 스크립트에 없는 것(=다운그레이드/리비전 삭제)
    return {
        "db_current": sorted(db),
        "expected_heads": sorted(expected),
        "missing": missing,
        "unknown": unknown,
        "drift": bool(missing or unknown),
    }


def _db_heads(database_url: str) -> list[str]:
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    engine = create_engine(database_url, poolclass=NullPool, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            return list(ctx.get_current_heads())
    finally:
        engine.dispose()


def _script_heads(ini_path: str) -> list[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(ini_path)
    return list(ScriptDirectory.from_config(cfg).get_heads())


def main() -> int:
    # Windows 콘솔 UTF-8 — import 시점이 아니라 실행 시점에만 stdout을 감싼다
    # (import 시 stdout 교체는 pytest capture를 깨뜨림).
    import io  # noqa: PLC0415
    import sys  # noqa: PLC0415
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Alembic applied-vs-head drift check")
    ap.add_argument("--alembic-ini", default="alembic.ini")
    ap.add_argument("--out", default="reports/migration_drift.json")
    ap.add_argument(
        "--exit-zero-on-fail", action="store_true",
        help="드리프트/오류여도 exit 0 (점검 리포트만 — 비차단 모드)",
    )
    args = ap.parse_args()

    result: dict
    try:
        from lloydk.config import settings  # noqa: PLC0415

        expected = _script_heads(args.alembic_ini)
        applied = _db_heads(settings.database_url)
        result = compare_heads(applied, expected)
        result["ok"] = not result["drift"]
        result["error"] = None
    except Exception as exc:  # noqa: BLE001
        # DB 미가용 등 — 점검 자체 실패. 드리프트 여부 미확정이므로 비-OK.
        result = {
            "db_current": [], "expected_heads": [], "missing": [], "unknown": [],
            "drift": None, "ok": False, "error": f"{type(exc).__name__}: {exc}",
        }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if result["error"]:
        print(f"[migration-drift] CHECK FAILED: {result['error']}")
    elif result["drift"]:
        print(
            f"[migration-drift] DRIFT: db={result['db_current']} expected={result['expected_heads']} "
            f"missing={result['missing']} unknown={result['unknown']} — run `alembic upgrade head`"
        )
    else:
        print(f"[migration-drift] OK: db at head {result['db_current']}")

    if args.exit_zero_on_fail:
        return 0
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

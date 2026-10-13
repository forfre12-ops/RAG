"""scripts/ 전체를 CI·배포 매니페스트·시험 연결 여부로 분류한다(2026-10-03,
제3자 검토서 "수동 실행 전용 스크립트 75개" 수치 재검증 목적).

분류(한 스크립트가 여러 칸에 동시에 들 수 있다):
  매니페스트 — deploy_manifest.toml 의 container.scripts/ops_scripts/bundle.root/not_in_image 중 하나
  CI         — .github/workflows/*.yml 이 파일명을 언급
  시험       — tests/ 가 파일명 또는 stem 을 언급(네 glob 패턴 전부)
  미연결     — 위 셋 다 없음(= 수동 실행 전용 추정)

사용: python scripts/audit_scripts_wiring.py
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
REPO = POC.parent
SCRIPTS = POC / "scripts"
SKIP_DIRS = {"archive", "sql", "__pycache__"}


def _manifest_names() -> set[str]:
    manifest = tomllib.loads((POC / "deploy_manifest.toml").read_text(encoding="utf-8"))
    names: set[str] = set()
    names |= set(manifest["container"]["scripts"]["allow"])
    names |= set(manifest["container"]["scripts"]["not_in_image"])
    names |= set(manifest["container"].get("ops_scripts", {}).get("allow", []))
    names |= set(manifest["bundle"]["root"]["scripts"])
    return names


def _ci_text() -> str:
    parts = []
    wf = REPO / ".github" / "workflows"
    if wf.is_dir():
        for p in wf.glob("*.yml"):
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _tests_text() -> str:
    parts = []
    for p in (POC / "tests").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def main() -> int:
    targets = sorted(
        p for p in SCRIPTS.rglob("*")
        if p.is_file()
        and p.suffix in (".py", ".sh")
        and not (set(p.relative_to(SCRIPTS).parts[:-1]) & SKIP_DIRS)
    )
    manifest_names = _manifest_names()
    ci_text = _ci_text()
    tests_text = _tests_text()

    rows = []
    for p in targets:
        name = p.name
        stem = p.stem
        in_manifest = name in manifest_names
        in_ci = bool(re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", ci_text))
        in_tests = bool(re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", tests_text)) or \
            bool(re.search(r"(?:from|import)\s+" + re.escape(stem) + r"(?![\w])", tests_text))
        rows.append((name, in_manifest, in_ci, in_tests))

    unwired = [r for r in rows if not (r[1] or r[2] or r[3])]

    print(f"scripts/ 최상위 대상(archive·sql·__pycache__ 제외): {len(rows)}개")
    print(f"  매니페스트 등재: {sum(r[1] for r in rows)}개")
    print(f"  CI 언급:        {sum(r[2] for r in rows)}개")
    print(f"  시험 언급:      {sum(r[3] for r in rows)}개")
    print(f"  셋 다 없음(미연결, 수동 실행 전용 추정): {len(unwired)}개")
    print()
    print("미연결 목록:")
    for name, *_ in unwired:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

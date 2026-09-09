# -*- coding: utf-8 -*-
"""시스템 폐기 — 구성요소를 재사용 불가능하게 지우고, 지웠다는 것을 증명한다.

근거: 국가정보원 「AI 보안 가이드북」(2025.12) 부록1 M28
    "AI시스템 폐기 시 AI모델·학습데이터·벡터DB·로그 등 구성요소의 재사용이
     불가능하도록 완전 삭제 대책을 마련하였는가?"

왜 이 도구가 필요한가(2026-09-08). 폐기는 한 번뿐이고 되돌릴 수 없다. 손으로 하면
빠뜨린 것을 나중에 알 수 없고, "다 지웠다"를 증명할 방법도 없다. 증명이 절차의 절반이다.

핵심은 **암호 소거(crypto-erase)** 다. 원본 문서는 이미 AES-256-GCM 으로 저장돼 있어
(STORAGE_ENCRYPTION_ENABLED, 운영 프로파일에서 강제 ON), 키를 파기하면 남은 암호문은
복구할 수 없다. 그래서 대용량 원본을 물리적으로 덮어쓸 필요가 없다 - 키 하나가 그 일을
대신한다. 나머지 구성요소는 볼륨·디렉터리·이미지 단위로 지운다.

⚠ 이 도구는 **키를 대신 지워 주지 않는다.** 키는 .env·비밀 저장소·백업·운영자 기록 등
  우리가 볼 수 없는 곳에도 있을 수 있다. 도구는 키가 있던 자리를 알려 주고, 운영자가
  파기했다고 확인해야만 진행한다(--key-destroyed). 우리가 모르는 사본을 지웠다고
  기록하는 것이 가장 위험하다.

기본은 dry-run 이다. 실제 삭제는 --execute 와 --confirm <프로젝트명> 을 함께 줘야 한다.

사용:
    # 무엇이 지워지는지만 본다 (기본)
    python scripts/decommission.py --project koipa-airgap

    # 실제 폐기 - 프로젝트명을 다시 적어 확인한다
    python scripts/decommission.py --project koipa-airgap \\
        --execute --confirm koipa-airgap --key-destroyed \\
        --operator "홍길동" --report 폐기확인서.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()

_POC = Path(__file__).resolve().parents[1]

# 배포가 만드는 named volume. docker-compose.airgap.yml + mariadb 오버레이 기준.
# 볼륨 이름은 compose 가 `<프로젝트>_<이름>` 으로 만든다.
_VOLUMES = (
    ("pgdata", "PostgreSQL - 문서 메타·분류 이력·감사 로그·벡터(pgvector)"),
    ("redisdata", "Redis - 잡 큐·캐시"),
    ("storagedata", "원본 문서 보관소 (암호문)"),
    ("golden_data", "골든 후보·검수 원장·사람 서명"),
    ("artifacts_out", "재학습 산출 모델"),
)

# 호스트 경로. 번들 배치에 따라 없을 수 있다 - 없으면 건너뛴다.
_HOST_PATHS = (
    ("models", "사전 동봉 모델 - 분류기 가중치·임베딩·HF 캐시"),
    ("datasets", "학습셋·평가셋·골든 후보 원본"),
    ("artifacts", "학습 체크포인트"),
)

_KEY_LOCATIONS = (
    ".env  의 STORAGE_ENCRYPTION_KEY",
    "컨테이너 환경변수 (docker inspect 에 남는다)",
    "운영자 비밀 저장소 · 인수인계 문서 · 백업본",
)


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _docker(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["docker", *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "docker 를 찾을 수 없다"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _volume_exists(name: str) -> bool:
    rc, _ = _docker("volume", "inspect", name)
    return rc == 0


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def survey(project: str) -> dict:
    """무엇이 남아 있는가. 삭제 전후로 같은 함수를 쓴다 - 그래야 검증이 성립한다."""
    volumes = []
    for short, why in _VOLUMES:
        name = f"{project}_{short}"
        volumes.append({"name": name, "why": why, "exists": _volume_exists(name)})

    paths = []
    for rel, why in _HOST_PATHS:
        p = _POC / rel
        paths.append({
            "path": str(p), "why": why, "exists": p.exists(),
            "bytes": _dir_size(p) if p.exists() else 0,
        })

    rc, out = _docker("ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                      "--format", "{{.Names}}")
    containers = [ln.strip() for ln in out.splitlines() if ln.strip()] if rc == 0 else []

    return {"volumes": volumes, "paths": paths, "containers": containers}


def _render(state: dict) -> None:
    log("  [볼륨]")
    for v in state["volumes"]:
        mark = "남음" if v["exists"] else "없음"
        log(f"    {mark:<4} {v['name']:<34} {v['why']}")
    log("  [호스트 경로]")
    for p in state["paths"]:
        mark = "남음" if p["exists"] else "없음"
        size = f"{p['bytes'] / 1e9:.2f}GB" if p["exists"] else "-"
        log(f"    {mark:<4} {p['path']:<34} {size:>9}  {p['why']}")
    log("  [컨테이너]")
    if state["containers"]:
        for c in state["containers"]:
            log(f"    남음 {c}")
    else:
        log("    없음")


def _remaining(state: dict) -> list[str]:
    out = [v["name"] for v in state["volumes"] if v["exists"]]
    out += [p["path"] for p in state["paths"] if p["exists"]]
    out += state["containers"]
    return out


def destroy(project: str, state: dict, *, keep_paths: bool) -> list[str]:
    """실제 삭제. 실패한 것을 돌려준다 - 조용히 넘어가지 않는다."""
    failed: list[str] = []

    log("\n[1/3] 컨테이너 정지 · 제거")
    rc, out = _docker("compose", "-p", project, "down", "--remove-orphans")
    if rc != 0:
        # compose 파일 없이 실행할 수도 있다 - 컨테이너를 직접 지운다.
        for name in state["containers"]:
            rc2, _ = _docker("rm", "-f", name)
            if rc2 != 0:
                failed.append(f"container:{name}")
    log(f"    {out.strip()[-200:] or '완료'}")

    log("\n[2/3] 볼륨 삭제")
    for v in state["volumes"]:
        if not v["exists"]:
            continue
        rc, out = _docker("volume", "rm", v["name"])
        if rc == 0:
            log(f"    삭제 {v['name']}")
        else:
            failed.append(f"volume:{v['name']}")
            log(f"    실패 {v['name']} - {out.strip()[:120]}")

    log("\n[3/3] 호스트 경로 삭제")
    if keep_paths:
        log("    건너뜀 (--keep-paths) - 모델·학습셋을 남긴다")
    else:
        for p in state["paths"]:
            if not p["exists"]:
                continue
            try:
                shutil.rmtree(p["path"])
                log(f"    삭제 {p['path']}")
            except OSError as exc:
                failed.append(f"path:{p['path']}")
                log(f"    실패 {p['path']} - {exc}")
    return failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="시스템 폐기 (M28) - 기본은 dry-run")
    ap.add_argument("--project", required=True, help="compose 프로젝트명 (예: koipa-airgap)")
    ap.add_argument("--execute", action="store_true", help="실제로 삭제한다")
    ap.add_argument("--confirm", help="--execute 와 함께 프로젝트명을 다시 적는다")
    ap.add_argument("--key-destroyed", action="store_true",
                    help="저장 암호화 키를 모든 사본에서 파기했음을 확인한다")
    ap.add_argument("--keep-paths", action="store_true",
                    help="호스트 경로(models·datasets·artifacts)는 남긴다")
    ap.add_argument("--operator", default="", help="폐기 수행자 (확인서에 기록)")
    ap.add_argument("--report", help="폐기 확인서를 JSON 으로 저장")
    args = ap.parse_args(argv)

    log("시스템 폐기 - 국정원 AI 보안 가이드북 부록1 M28")
    log("=" * 74)
    log(f"프로젝트: {args.project}")
    log()

    log("■ 암호 소거 - 원본 문서는 이것으로 복구 불가가 된다")
    log("  원본은 AES-256-GCM 으로 저장돼 있다. 키를 파기하면 암호문은 되살릴 수 없다.")
    log("  키가 있을 수 있는 자리(도구가 대신 지우지 않는다):")
    for loc in _KEY_LOCATIONS:
        log(f"    - {loc}")
    log()

    log("■ 현재 상태")
    before = survey(args.project)
    _render(before)

    if not args.execute:
        log()
        log("dry-run 이다. 아무것도 지우지 않았다.")
        log(f"실제 폐기: --execute --confirm {args.project} --key-destroyed")
        return 0

    if args.confirm != args.project:
        log()
        log(f"중단 - --confirm 이 프로젝트명과 다르다(받은 값: {args.confirm!r}).")
        log("  되돌릴 수 없는 작업이라 이름을 두 번 적게 한다.")
        return 2

    if not args.key_destroyed:
        log()
        log("중단 - 저장 암호화 키 파기가 확인되지 않았다(--key-destroyed).")
        log("  키가 남으면 백업된 암호문이 나중에 복호될 수 있다. 암호 소거가 이 절차의 핵심이다.")
        return 2

    failed = destroy(args.project, before, keep_paths=args.keep_paths)

    log("\n■ 삭제 후 확인")
    after = survey(args.project)
    _render(after)

    remaining = _remaining(after)
    if args.keep_paths:
        remaining = [r for r in remaining if not any(r == p["path"] for p in after["paths"])]

    log()
    if failed or remaining:
        log(f"미완료 - 실패 {len(failed)}건 · 남은 것 {len(remaining)}건")
        for item in (failed + remaining)[:10]:
            log(f"    {item}")
        status = "incomplete"
        rc = 1
    else:
        log("완료 - 대상이 모두 지워졌다.")
        status = "complete"
        rc = 0

    if args.report:
        report = {
            "standard": "국가정보원 AI 보안 가이드북(2025.12) 부록1 M28",
            "project": args.project,
            "operator": args.operator,
            "crypto_erase_attested": bool(args.key_destroyed),
            "kept_host_paths": bool(args.keep_paths),
            "status": status,
            "failed": failed,
            "remaining": remaining,
            "before": before,
            "after": after,
        }
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"확인서: {args.report}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

"""DR 백업/복구 공용 — 실행 중 koipa compose 컨테이너 자동탐지.

scripts/ 하위 백업·복구 스크립트가 공유한다(각 스크립트는 `python scripts/X.py` 로 실행돼
sys.path[0]=scripts 라 상대 import 가능). 컨테이너명이 배포마다 다르다:
  dev=koipa-poc-* · airgap=koipa-airgap-* · dual=koipa-jjw-*·koipa-cust-*
그래서 하드코딩 기본값(koipa-poc-postgres-1)이 airgap·dual 에서 빗나가 백업/복구가 조용히
엉뚱한(또는 없는) 컨테이너를 가리키던 문제를, 실행시간 라벨 자동탐지로 해소한다.

주의: 이 모듈은 백업 스크립트가 **런타임 import 하는 실배포 의존**이라 `_` 프리픽스를 쓰지
않는다(.gitignore 의 scripts/_*.py 는 dev/bench 임시 스크립트용 — 여기 해당 없음).
"""

from __future__ import annotations

import shutil
import subprocess


def list_koipa_containers() -> list[dict[str, str]]:
    """실행 중 koipa compose 컨테이너 — [{name, project, service}, ...].

    docker 미가용/실패 시 빈 리스트(자동탐지 실패는 호출측에서 fail-closed 처리).
    """
    if shutil.which("docker") is None:
        return []
    fmt = '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}'
    r = subprocess.run(
        ["docker", "ps", "--format", fmt], capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        return []
    out: list[dict[str, str]] = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, project, service = (p.strip() for p in parts)
        if not name or not project.startswith("koipa"):
            continue
        out.append({"name": name, "project": project, "service": service})
    return out


def autodetect_container(
    services: tuple[str, ...],
    containers: list[dict[str, str]] | None = None,
) -> str | None:
    """지정 service(우선순위 순) 컨테이너 1개를 자동 선택.

    services: 예 ("postgres",) 또는 ("worker", "api") — 앞선 것이 우선.
    스택이 2개 이상(예: dual 테스트서버 jjw+cust)이면 모호 → None(명시 지정 필요).
    """
    containers = list_koipa_containers() if containers is None else containers
    capable = [c for c in containers if c["service"] in services]
    projects = {c["project"] for c in capable}
    if len(projects) != 1:
        return None
    for svc in services:  # services 순서 = 우선순위
        for c in capable:
            if c["service"] == svc:
                return c["name"]
    return None


def group_stacks(
    containers: list[dict[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    """프로젝트(스택)별로 pg/storage 백업에 쓸 컨테이너명을 묶어 반환.

    반환: {project: {"postgres": <name>, "storage": <name>}} — 각 스택에 pg 와
    storage(worker 우선, 없으면 api) 컨테이너가 모두 있을 때만 항목 포함.
    """
    containers = list_koipa_containers() if containers is None else containers
    by_project: dict[str, dict[str, list[str]]] = {}
    for c in containers:
        by_project.setdefault(c["project"], {}).setdefault(c["service"], []).append(c["name"])

    stacks: dict[str, dict[str, str]] = {}
    for project, svcs in by_project.items():
        pg = svcs.get("postgres", [])
        storage = svcs.get("worker") or svcs.get("api") or []
        if pg and storage:
            stacks[project] = {"postgres": pg[0], "storage": storage[0]}
    return stacks

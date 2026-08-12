"""B5 — OpenAPI yaml ↔ 실 FastAPI 라우터 정합성 검사.

용도:
- doc/03_openapi_koipa_kl.yaml 에 정의된 path/method가 실 app에 모두 구현됐는가
- 반대로 app에 있지만 yaml에 없는 path (drift) 식별
- KL 회신 K2 검토 의견 처리 시 빠른 영향 분석

산출:
- 표준 출력 + JSON 산출 (poc/reports/openapi_consistency.json)
- 미정합 1건 이상 시 exit 1 (CI 게이트 가능)

사용:
  python scripts/openapi_consistency.py                # 검사 + 표 출력
  python scripts/openapi_consistency.py --json out.json
  python scripts/openapi_consistency.py --strict       # drift 1건이라도 있으면 exit 1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Windows cp949 UTF-8 자동 전환
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
POC_ROOT = HERE.parent
REPO_ROOT = POC_ROOT.parent
sys.path.insert(0, str(POC_ROOT / "src"))

DEFAULT_YAML = REPO_ROOT / "doc" / "03_openapi_koipa_kl.yaml"


def _parse_yaml_paths(yaml_path: Path) -> tuple[set[tuple[str, str]], str]:
    """YAML에서 paths 섹션의 (METHOD, PATH) + server URL prefix 추출. PyYAML 의존 회피.

    spec format:
      servers:
        - url: http://koipa-api:8000/api/v1
      paths:
        /classify:
          post:
            ...
    """
    if not yaml_path.exists():
        return set(), ""
    out: set[tuple[str, str]] = set()
    in_paths = False
    in_servers = False
    current_path: str | None = None
    server_prefix = ""
    methods = {"get", "post", "put", "delete", "patch", "options", "head"}

    for raw_line in yaml_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # servers: 섹션 — 첫 url에서 path prefix 추출
        if stripped.startswith("servers:") and indent == 0:
            in_servers = True
            in_paths = False
            continue
        if in_servers:
            m = re.match(r"-\s*url:\s*(\S+)", stripped)
            if m and not server_prefix:
                url = m.group(1).strip().strip('"\'')
                # URL의 path 부분만 추출 — 예: http://host/api/v1 → /api/v1
                m2 = re.match(r"https?://[^/]+(/[^\s]*)", url)
                if m2:
                    p = m2.group(1).rstrip("/")
                    if p and p != "/":
                        server_prefix = p
            if indent == 0 and not stripped.startswith("-"):
                in_servers = False

        # paths: 섹션 시작
        if stripped == "paths:" and indent == 0:
            in_paths = True
            in_servers = False
            continue

        if not in_paths:
            continue

        # paths 섹션 종료 (들여쓰기 0으로 돌아가면)
        if indent == 0 and ":" in stripped and not stripped.startswith("/"):
            in_paths = False
            current_path = None
            continue

        # path 라인: "  /classify:" (indent 2)
        m = re.match(r"^(/[^\s:]*):\s*$", stripped)
        if m and indent == 2:
            current_path = m.group(1)
            continue

        # method 라인: "    post:" (indent 4)
        m = re.match(r"^([a-z]+):\s*$", stripped)
        if m and current_path and indent == 4:
            method = m.group(1).lower()
            if method in methods:
                full_path = f"{server_prefix}{current_path}" if server_prefix else current_path
                out.add((method.upper(), full_path))

    return out, server_prefix


# 라우터에 등록되지만 OpenAPI YAML 명세 범위 밖으로 의도적 제외된 경로.
# - /metrics-prom : Prometheus 스크랩 전용 (내부 observability, 외부 API 계약 아님)
# - /docs /redoc  : Swagger UI (개발용, 고객사 계약 명세 아님)
# - /openapi.json : FastAPI 자동 생성 (YAML 명세와 중복 관리 방지)
# - /demo/*       : 데모 콘솔 정적 파일 (StaticFiles)
_ROUTER_IGNORE_PREFIXES: tuple[str, ...] = (
    "/docs",
    "/redoc",
    "/openapi.json",
)
_ROUTER_IGNORE_EXACT: set[str] = {
    "/api/v1/metrics-prom",
    "/api/v1/openapi.json",
    # 내부 운영/관리 라우트 — KL 연동(외부) 계약 명세(03_openapi_koipa_kl.yaml) 범위 밖이며,
    # 전체 라우트는 부록B API 명세(DEF-2026-39, 48건 전수 문서화)가 다룬다.
    "/api/v1/admin/demo/purge",   # 데모 데이터 purge — 관리·파괴적, 외부 계약 아님
    "/api/v1/dashboard/summary",  # 운영 대시보드 집계 — 내부 관측성
    "/api/v1/rag/search",         # LLM-free 내부 검색 — 외부 KL API 아님
}


def _is_ignored(path: str) -> bool:
    """의도적으로 YAML 명세에서 제외된 경로 여부."""
    if path in _ROUTER_IGNORE_EXACT:
        return True
    for prefix in _ROUTER_IGNORE_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    # 데모 콘솔 정적 파일 (/demo로 시작)
    if path == "/demo" or path.startswith("/demo/"):
        return True
    return False


def _collect_router_paths() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """FastAPI app에서 모든 라우트의 (METHOD, PATH) 추출.

    Returns:
        (active_paths, training_only_paths)
        - active_paths: 현재 프로파일에서 활성화된 경로 (ignore 제외)
        - training_only_paths: enable_training=True 일 때만 등록되는 경로
    """
    try:
        from koipa.api.app import app  # noqa: PLC0415
        from koipa.config import settings  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR: koipa.api.app import 실패: {exc}")
        return set(), set()

    training_enabled = getattr(settings, "enable_training", False)

    out: set[tuple[str, str]] = set()
    # Starlette 신버전은 include_router 결과를 path 없는 래퍼로 감싸 route.path 가 None →
    # app.routes 순회는 include_router 경로를 전부 놓쳐 43개를 '미구현'으로 오탐한다.
    # 공개 API app.openapi() 로 경로를 수집한다(test_deploy_profile.py 와 동일 방식, DEF-2026-38).
    _http_methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
    for path, path_item in (app.openapi().get("paths") or {}).items():
        if _is_ignored(path):
            continue
        for method in path_item:
            m = method.upper()
            if m not in _http_methods:
                continue
            out.add((m, path))

    # training 라우터는 profile 조건부 — YAML에는 있지만 현재 환경에서 미등록일 수 있음
    training_paths: set[tuple[str, str]] = set()
    if not training_enabled:
        # YAML의 /train/* 경로는 full-train 프로파일 전용으로 표시
        training_patterns = ("/api/v1/train", "/train")
        for route in []:  # 실제 라우터에 없으므로 빈 루프 — YAML에서 제거 예정
            pass
        # YAML only로 나타날 training 경로들을 식별해 조건부 제외 허용
        training_paths = {
            (m.upper(), p) for m, p in out
            if any(p.startswith(tp) for tp in training_patterns)
        }

    return out, training_paths


def _normalize_path(path: str) -> str:
    """OpenAPI {param} ↔ FastAPI {param} 정합 — 둘 다 동일 형식이라 단순 캐스팅."""
    # 필요 시 추가 정규화 (예: trailing slash) 여기서.
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    return path


def diff(
    yaml_set: set[tuple[str, str]],
    router_set: set[tuple[str, str]],
    *,
    skip_yaml_prefixes: tuple[str, ...] = (),
) -> dict:
    """yaml ↔ router 차이 계산.

    Args:
        skip_yaml_prefixes: 이 prefix로 시작하는 YAML 경로는 비교에서 제외
                            (예: training 라우터 profile-conditional 처리).
    """
    yaml_norm = {(m, _normalize_path(p)) for m, p in yaml_set}
    router_norm = {(m, _normalize_path(p)) for m, p in router_set}

    # profile-conditional 제외 (예: training 라우터)
    if skip_yaml_prefixes:
        yaml_norm = {
            (m, p) for m, p in yaml_norm
            if not any(p.startswith(pfx) for pfx in skip_yaml_prefixes)
        }

    only_yaml = yaml_norm - router_norm  # yaml에만 있음 (미구현)
    only_router = router_norm - yaml_norm  # router에만 있음 (스펙 누락)
    both = yaml_norm & router_norm

    return {
        "yaml_count": len(yaml_norm),
        "router_count": len(router_norm),
        "common": sorted([f"{m} {p}" for m, p in both]),
        "only_in_yaml": sorted([f"{m} {p}" for m, p in only_yaml]),
        "only_in_router": sorted([f"{m} {p}" for m, p in only_router]),
        "match_rate": len(both) / len(yaml_norm) if yaml_norm else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yaml", default=str(DEFAULT_YAML))
    ap.add_argument("--json", dest="json_out", default=str(POC_ROOT / "reports" / "openapi_consistency.json"))
    ap.add_argument("--strict", action="store_true", help="drift 1건 이상 시 exit 1")
    args = ap.parse_args()

    print("=" * 70)
    print("KOIPA AI — OpenAPI ↔ Router 정합성 (B5)")
    print("=" * 70)

    yaml_paths, server_prefix = _parse_yaml_paths(Path(args.yaml))
    router_paths, _training_paths = _collect_router_paths()
    if server_prefix:
        print(f"  YAML server prefix: {server_prefix}")

    # training 라우터는 full-train 프로파일 전용 — 현재 프로파일에서 비활성이면
    # YAML의 /train/* 경로를 strict 비교에서 제외 (profile-conditional)
    try:
        from koipa.config import settings as _s  # noqa: PLC0415
        _training_enabled = getattr(_s, "enable_training", False)
    except Exception:  # noqa: BLE001
        _training_enabled = False

    skip_prefixes: tuple[str, ...] = ()
    if not _training_enabled:
        skip_prefixes = (f"{server_prefix}/train", "/api/v1/train", "/train")
        print("  ℹ training 라우터 비활성 (enable_training=False) — /train/* YAML 경로 제외")

    result = diff(yaml_paths, router_paths, skip_yaml_prefixes=skip_prefixes)

    print(f"  YAML 정의   : {result['yaml_count']} 엔드포인트")
    print(f"  Router 구현 : {result['router_count']} 엔드포인트")
    print(f"  일치 (양쪽) : {len(result['common'])}")
    print(f"  미구현 (yaml only)  : {len(result['only_in_yaml'])}")
    print(f"  스펙 누락 (router only): {len(result['only_in_router'])}")
    print(f"  매칭률      : {result['match_rate']*100:.1f}%")
    print()

    if result["only_in_yaml"]:
        print("  ⚠ YAML 정의 있는데 라우터 미구현:")
        for s in result["only_in_yaml"]:
            print(f"    - {s}")
        print()

    if result["only_in_router"]:
        print("  ⚠ 라우터에만 있는 (스펙 누락) 엔드포인트:")
        for s in result["only_in_router"]:
            print(f"    - {s}")
        print()

    out_path = Path(args.json_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  JSON → {out_path}")

    drift = len(result["only_in_yaml"]) + len(result["only_in_router"])
    if args.strict and drift > 0:
        print(f"\n  --strict: drift {drift}건 발견 → exit 1")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

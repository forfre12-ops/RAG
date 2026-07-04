"""폐쇄망 자기완비 번들 빌더.

doc/12_폐쇄망_배포_설계.md §3·§4 구현.

두 가지 모드:
  --dry-run : 다운로드·빌드 없이 manifest.yaml만 미리 생성 + 체크리스트·예상 크기 출력
  (기본)    : 실제 docker save / huggingface 다운로드 / pip download 실행 (네트워크 필요)

핵심 설계:
  - docker-compose.yml에서 image: 라인 파싱 → 컴포넌트 자동 추출
  - poc/src/lloydk/config.py의 모델명 정적 분석 (import 없이 텍스트 파싱)
  - dry-run은 외부 네트워크 호출 0 → CI에서 PR마다 검증 가능
  - manifest.yaml 스키마는 doc/12 §3.3 따름

사용 예:
  # 회신 전 검증
  python scripts/build_offline_bundle.py --version 1.0.0-rc1 --dry-run

  # 실제 빌드 (운영 환경 확정 후, 외부망에서)
  python scripts/build_offline_bundle.py --version 1.0.0 --output ./dist/bundle
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent  # poc/


# ─────────────────────────────────────────────────────────────
# 데이터 모델 (manifest.yaml 스키마, doc/12 §3.3)
# ─────────────────────────────────────────────────────────────


@dataclass
class ComponentEntry:
    image: str
    version: str
    sha256: str | None = None  # dry-run에선 None


@dataclass
class ModelEntry:
    name: str
    dim: int | None
    sha256: str | None
    license: str
    role: str  # classifier | classifier_trained | embedding | llm | embedding_fallback
    source_path: str | None = None  # 빌드 호스트의 원본 경로(학습 모델 디렉토리 등). 복사 대상.


@dataclass
class PluginEntry:
    name: str
    version: str
    sha256: str | None = None


@dataclass
class BundlePolicies:
    vector_backend_default: str = "pg"   # §03 ES→Postgres 단일화(2026-06-24). ES 폐기.
    llm_provider_default: str = "vllm"
    qwen3_thinking_mode: bool = False


@dataclass
class SecurityScan:
    # [정직화] 빌드 시 실제 취약점 스캔을 수행하지 않는다. 과거 default 'trivy (dry-run skipped)'는
    # trivy 가 개입한 것처럼 암시해, 안 한 스캔을 매니페스트가 한 것처럼 실었다. scanned=False 로
    # 스캔 미수행을 명시한다(감사·컴플라이언스 오독 차단). 실제 trivy/grype 실행·SARIF 파싱 배선은
    # CI 후속 과제(도구 설치 필요) — 스캔이 실제로 돌면 populate 로 scanned/scanned_with/CVE 채운다.
    scanned: bool = False
    scanned_with: str = "none (no vulnerability scan performed at build time)"
    scan_date: str = ""
    critical_cves: int = 0
    high_cves: int = 0


@dataclass
class BundleManifest:
    bundle_name: str
    version: str
    build_date: str
    git_commit: str
    target_env: str
    dry_run: bool
    components: dict[str, ComponentEntry] = field(default_factory=dict)
    models: list[ModelEntry] = field(default_factory=list)
    es_plugins: list[PluginEntry] = field(default_factory=list)
    policies: BundlePolicies = field(default_factory=BundlePolicies)
    security: SecurityScan = field(default_factory=SecurityScan)
    estimated_size_gb: float = 0.0
    files_expected: list[str] = field(default_factory=list)
    # 관측성 스택 이미지(best-effort 동봉) — 안전 알림 소비자. 빈 리스트면 미포함.
    observability_images: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "bundle": {
                "name": self.bundle_name,
                "version": self.version,
                "build_date": self.build_date,
                "git_commit": self.git_commit,
                "target_env": self.target_env,
                "dry_run": self.dry_run,
            },
            "components": {k: asdict(v) for k, v in self.components.items()},
            "models": [asdict(m) for m in self.models],
            "es_plugins": [asdict(p) for p in self.es_plugins],
            "policies": asdict(self.policies),
            "security": asdict(self.security),
            "estimated_size_gb": self.estimated_size_gb,
            "files_expected": self.files_expected,
            "observability_images": self.observability_images,
        }


# ─────────────────────────────────────────────────────────────
# 추출 로직 (외부 네트워크 0)
# ─────────────────────────────────────────────────────────────

# image: 값만 포착(뒤따르는 인라인 주석 허용). 끝 앵커 `$` 를 쓰면 `image: x  # 주석` 라인이
# 통째로 매칭 실패해 그 서비스가 번들에서 통으로 누락된다 — postgres/api 가 폐쇄망 번들에서
# 빠지던 실제 회귀 원인이었다. `(\S+)` 가 공백 앞에서 멈추므로 인라인 주석은 자연히 배제된다.
_IMAGE_LINE = re.compile(r"^\s*image:\s*(\S+)")
_MODEL_RE = re.compile(r"^\s*(\w+_model)\s*:\s*str\s*=\s*\"([^\"]+)\"", re.MULTILINE)
# `vllm_model: str = "..."`, `classifier_base_model: str = "..."` 둘 다 잡음
_ANY_MODEL_RE = re.compile(
    r"^\s*([a-z_]+)\s*:\s*str\s*=\s*\"([^\"]+)\"\s*$",
    re.MULTILINE,
)

# 폐쇄망 기동에 필수인 코어 서비스 — 번들에 이미지 tar 가 반드시 있어야 한다.
# nginx-mtls 는 `--profile mtls` 옵션이라 필수에서 제외. main() 이 이 목록으로 dry-run/실빌드
# 양쪽에서 누락을 fail-closed 검사한다(CI dry-run 이 postgres 누락을 잡도록).
_REQUIRED_CORE_SERVICES = ("postgres", "redis", "api", "worker", "beat")

# 관측성 스택 이미지 — infra/observability/docker-compose.observability.airgap.yml 과 태그 동기.
# best-effort 동봉(핵심 아님): 저장 실패 시 경고만(빌드 실패 아님). 안전 알림(FnrSpike·
# AuditChainBroken·KillGateTripped 등)의 폐쇄망 소비자. --skip-observability 로 제외 가능.
_OBSERVABILITY_IMAGES = (
    "prom/prometheus:v2.55.1",
    "grafana/grafana:11.3.0",
    "grafana/loki:3.2.1",
    "grafana/promtail:3.2.1",
    "prometheuscommunity/postgres-exporter:v0.16.0",
    "oliver006/redis_exporter:v1.66.0",
)

# docker-compose 스타일 `${NAME}` / `${NAME:-default}` 치환용.
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env_vars(value: str, overrides: dict[str, str] | None = None) -> str:
    """compose 의 `${NAME}` / `${NAME:-default}` 치환.

    우선순위: overrides > 환경변수(비어있지 않을 때) > default > 빈 문자열.
    번들러는 실제 태그를 알아야 docker save/load 가 일치한다 — `${IMAGE_TAG:-1.0.0-rc1}` 을
    리터럴로 캡처하면 `docker save lloydk-worker:${IMAGE_TAG:-1.0.0-rc1}` 가 존재불가 태그로
    실패한다. overrides 로 번들 --version 을 IMAGE_TAG 에 주입해 save/load 태그를 맞춘다.
    """
    overrides = overrides or {}

    def _repl(m: "re.Match") -> str:
        name, default = m.group(1), m.group(2)
        if name in overrides:
            return overrides[name]
        env_val = os.environ.get(name)
        if env_val:
            return env_val
        return default if default is not None else ""

    return _VAR_RE.sub(_repl, value)


def extract_components_from_compose(
    compose_path: Path, var_overrides: dict[str, str] | None = None,
) -> dict[str, ComponentEntry]:
    """docker-compose.yml에서 image: 라인 파싱.

    YAML 파서를 쓰지 않는 이유: PoC 환경에 pyyaml이 없어도 동작해야 함.
    `${VAR:-default}` 는 _expand_env_vars 로 확장(var_overrides 우선) — 미확장 리터럴
    태그로 docker save 가 실패하던 회귀 차단.
    """
    if not compose_path.exists():
        return {}
    components: dict[str, ComponentEntry] = {}
    current_service: str | None = None
    service_indent: int | None = None

    for raw in compose_path.read_text(encoding="utf-8").splitlines():
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(stripped)

        # 서비스 식별: services: 하위 2칸 들여쓰기 이름
        if stripped.endswith(":") and not stripped.startswith("-"):
            name = stripped[:-1]
            # 들여쓰기 깊이 변화로 새 서비스 진입 추정
            if service_indent is None and current_service is None and name == "services":
                service_indent = -1  # services 자체
                continue
            if service_indent is not None and indent == 2:
                current_service = name
                continue

        # image: 추출
        if current_service and indent >= 4:
            m = _IMAGE_LINE.match(raw)
            if m:
                full = _expand_env_vars(m.group(1), var_overrides)
                image, _, version = full.partition(":")
                if not version:
                    version = "latest"
                components[current_service] = ComponentEntry(image=full, version=version)
    return components


# 모델 라이선스 + 임베딩 차원 룩업 테이블 (외부 API 호출 회피)
_MODEL_META: dict[str, dict] = {
    "kakaobank/kf-deberta-base":            {"dim": None, "license": "MIT",        "role": "classifier"},
    "monologg/koelectra-base-v3-discriminator": {"dim": None, "license": "Apache-2.0", "role": "classifier_lightweight"},
    "nlpai-lab/KURE-v1":                    {"dim": 1024, "license": "MIT",        "role": "embedding"},
    "BAAI/bge-m3":                          {"dim": 1024, "license": "MIT",        "role": "embedding_fallback"},
    "Qwen/Qwen3-14B":                       {"dim": None, "license": "Apache-2.0", "role": "llm"},
    "Qwen/Qwen3-14B-Instruct-AWQ":          {"dim": None, "license": "Apache-2.0", "role": "llm"},
}


def extract_models_from_config(config_path: Path) -> list[ModelEntry]:
    """config.py를 텍스트 파싱하여 모델명 추출.

    `*_model: str = "..."` 패턴 + 사전 정의된 메타로 ModelEntry 구성.
    """
    if not config_path.exists():
        return []
    text = config_path.read_text(encoding="utf-8")
    models: list[ModelEntry] = []
    seen: set[str] = set()
    for m in _ANY_MODEL_RE.finditer(text):
        field_name, model_name = m.group(1), m.group(2)
        if "model" not in field_name:
            continue
        # Anthropic·OpenAI 등 상용 모델은 폐쇄망 번들 제외
        if "/" not in model_name and not model_name.startswith("Qwen"):
            continue
        if model_name in seen:
            continue
        seen.add(model_name)
        meta = _MODEL_META.get(model_name, {})
        models.append(
            ModelEntry(
                name=model_name,
                dim=meta.get("dim"),
                sha256=None,
                license=meta.get("license", "UNKNOWN"),
                role=meta.get("role", "unknown"),
            )
        )
    return models


def resolve_classifier_model_dir(explicit: str | None) -> Path | None:
    """학습된 분류기 가중치 디렉토리 해석 — 폐쇄망 번들에 동봉할 대상.

    우선순위: --classifier-model-dir 인자 → env CLASSIFIER_MODEL_DIR →
    env LLOYDK_CLASSIFIER_MODEL_DIR. 미설정이면 None(베이스 모델만 번들 — 경고 대상).
    서빙은 이 디렉토리에서 가중치 + temperature.json(보정)을 로드하므로, 빠지면
    폐쇄망에서 미학습·무보정으로 동작한다(#40).
    """
    import os  # noqa: PLC0415
    cand = explicit or os.environ.get("CLASSIFIER_MODEL_DIR") or os.environ.get("LLOYDK_CLASSIFIER_MODEL_DIR")
    if not cand:
        return None
    p = Path(cand)
    return p if p.exists() else None


# 컴포넌트별 예상 크기 (GB, doc/12 §3.2 기반 추정)
_COMPONENT_SIZE_GB: dict[str, float] = {
    "api": 1.5,
    "worker": 1.5,
    "postgres": 0.4,
    # elasticsearch 제거 — §03 ES→PG 단일화. (dev compose 잔존 minio/mlflow는 추정용으로만 유지)
    "minio": 0.2,
    "redis": 0.1,
    "mlflow": 0.6,
}

_MODEL_SIZE_GB: dict[str, float] = {
    "classifier": 0.7,
    "classifier_trained": 0.7,
    "classifier_lightweight": 0.5,
    "embedding": 2.0,
    "embedding_fallback": 2.0,
    "llm": 10.0,  # Qwen3-14B AWQ
    "unknown": 0.5,
}


def estimate_total_size(
    components: dict[str, ComponentEntry],
    models: list[ModelEntry],
    plugins: list[PluginEntry],
) -> float:
    total = sum(_COMPONENT_SIZE_GB.get(s, 0.5) for s in components)
    total += sum(_MODEL_SIZE_GB.get(m.role, 0.5) for m in models)
    total += 1.5  # wheels
    total += 0.1 * max(len(plugins), 1)  # ES 플러그인 (대략 100MB/개)
    total += 0.1  # configs / docs
    return round(total, 1)


def expected_files(
    components: dict[str, ComponentEntry],
    models: list[ModelEntry],
    observability_images: list[str] | None = None,
) -> list[str]:
    files: list[str] = ["README.md", "install.sh", "verify.sh", "manifest.yaml", "CHECKSUMS.sha256"]
    for svc in components:
        files.append(f"docker-images/{svc}.tar")
    for m in models:
        files.append(f"models/{m.name.replace('/', '-')}/")
    files.extend([
        "python-deps/wheels/",
        "python-deps/_requirements_no_torch.txt",
        "infra-config/docker-compose.yml",
        "infra-config/docker-compose.airgap.yml",
        "infra-config/.env.template",
        "db-migrations/alembic/",  # baseline + 후속 revision 전체 (init.sql 폐기)
        "docs/INSTALL.md",
        "docs/OPERATION.md",
        "docs/TROUBLESHOOTING.md",
        "licenses/third-party-licenses.txt",
    ])
    # 관측성 스택(동봉 시) — 설정은 항상, 이미지는 best-effort.
    if observability_images:
        files.extend([
            "observability/docker-compose.observability.airgap.yml",
            "observability/prometheus.yml",
            "observability/alert_rules.yml",
            "observability/grafana/",
        ])
        for img in observability_images:
            files.append(f"docker-images/obs-{_obs_tar_name(img)}.tar")
    return files


def _obs_tar_name(image: str) -> str:
    """관측성 이미지 ref 를 tar 파일명 조각으로 정규화(prom/prometheus:v2.55.1 → prometheus-v2.55.1)."""
    ref = image.split("/")[-1]
    return ref.replace(":", "-")


# ─────────────────────────────────────────────────────────────
# Git commit 추출 (실패해도 진행)
# ─────────────────────────────────────────────────────────────


def get_git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, text=True, timeout=3,
        ).strip()
        return out
    except Exception:  # noqa: BLE001
        return "unknown"


# ─────────────────────────────────────────────────────────────
# manifest 빌드
# ─────────────────────────────────────────────────────────────


def build_manifest(
    *,
    version: str,
    target_env: str,
    dry_run: bool,
    compose_path: Path,
    config_path: Path,
    classifier_model_dir: Path | None = None,
    include_observability: bool = True,
) -> BundleManifest:
    # IMAGE_TAG 를 --version 으로 주입 → api/worker/beat 태그가 docker save/load 와 일치.
    components = extract_components_from_compose(
        compose_path, var_overrides={"IMAGE_TAG": version},
    )

    # 안전망: 정상 파싱되면 api/worker 는 이미 존재하므로 no-op. 파싱이 놓친 경우에만
    # --version 태그로 폴백한다(과거엔 인라인 주석 파싱실패를 이 setdefault 가 우연히 덮어
    # 누락을 감췄다 — 이제 파싱이 정상이라 실제 폴백은 거의 발생하지 않는다).
    for svc in ("api", "worker"):
        components.setdefault(
            svc,
            ComponentEntry(image=f"lloydk-{svc}:{version}", version=version),
        )

    models = extract_models_from_config(config_path)
    # #40: 학습된 분류기 가중치 + temperature.json을 번들에 동봉. config.py가 가리키는
    # HF 베이스 모델만으론 폐쇄망에서 미학습·무보정으로 동작한다.
    if classifier_model_dir is not None:
        has_temp = (classifier_model_dir / "temperature.json").exists()
        models.append(ModelEntry(
            name="classifier-trained",
            dim=None,
            sha256=None,
            license="internal-trained",
            role="classifier_trained",
            source_path=str(classifier_model_dir),
        ))
        if not has_temp:
            print(
                f"  [WARN] {classifier_model_dir}/temperature.json 없음 — 보정(temperature) "
                "미동봉. 서빙이 T=1.0(무보정)로 동작합니다. calibrate_classifier.py 실행 권장.",
                file=sys.stderr,
            )
    plugins: list[PluginEntry] = []  # ES 제거(의사결정_대장 §03 ⓑ) — 번들에 검색엔진 플러그인 없음
    obs_images = list(_OBSERVABILITY_IMAGES) if include_observability else []
    size = estimate_total_size(components, models, plugins)
    files = expected_files(components, models, obs_images)

    return BundleManifest(
        bundle_name="lloydk-airgap-bundle",
        version=version,
        build_date=time.strftime("%Y-%m-%d"),
        git_commit=get_git_commit(),
        target_env=target_env,
        dry_run=dry_run,
        components=components,
        models=models,
        es_plugins=plugins,
        estimated_size_gb=size,
        files_expected=files,
        observability_images=obs_images,
    )


# ─────────────────────────────────────────────────────────────
# manifest 직렬화 (pyyaml 없이 동작하도록 JSON·간이 YAML 출력 모두 지원)
# ─────────────────────────────────────────────────────────────


def write_manifest(manifest: BundleManifest, out_dir: Path, *, also_json: bool = True) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    # JSON (스키마 검증 친화)
    if also_json:
        json_path = out_dir / "manifest.json"
        json_path.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths["json"] = json_path

    # YAML (사람 친화) — pyyaml 있으면 그걸로, 없으면 간이 변환
    yaml_path = out_dir / "manifest.yaml"
    try:
        import yaml  # noqa: PLC0415

        yaml_text = yaml.safe_dump(manifest.to_dict(), allow_unicode=True, sort_keys=False)
    except ImportError:
        yaml_text = _simple_yaml_dump(manifest.to_dict())
    yaml_path.write_text(yaml_text, encoding="utf-8")
    paths["yaml"] = yaml_path

    return paths


def _simple_yaml_dump(data, indent: int = 0) -> str:
    """pyyaml 없는 환경에서 최소 동작하는 dumper."""
    lines: list[str] = []
    pad = "  " * indent
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(_simple_yaml_dump(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {_scalar(v)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                first = True
                for k, v in item.items():
                    prefix = f"{pad}- " if first else f"{pad}  "
                    lines.append(f"{prefix}{k}: {_scalar(v)}")
                    first = False
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    else:
        lines.append(f"{pad}{_scalar(data)}")
    return "\n".join(lines)


def _scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if any(c in s for c in [":", "#", "[", "]", "{", "}", "\n"]):
        return json.dumps(s, ensure_ascii=False)
    return s


# ─────────────────────────────────────────────────────────────
# CHECKSUMS (dry-run에선 manifest만 해시)
# ─────────────────────────────────────────────────────────────


def write_checksums(out_dir: Path, files: list[Path]) -> Path:
    cs_path = out_dir / "CHECKSUMS.sha256"
    lines: list[str] = []
    for f in sorted(files):
        if not f.exists():
            continue
        h = hashlib.sha256()
        with f.open("rb") as fp:
            for chunk in iter(lambda: fp.read(65536), b""):
                h.update(chunk)
        rel = f.relative_to(out_dir).as_posix()
        lines.append(f"{h.hexdigest()}  {rel}")
    cs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cs_path


# ─────────────────────────────────────────────────────────────
# 체크리스트 출력 (사람용)
# ─────────────────────────────────────────────────────────────


def print_checklist(manifest: BundleManifest, *, stream=sys.stdout) -> None:
    """ASCII-only 출력 (Windows cp949 콘솔 호환)."""
    p = lambda s: print(s, file=stream)  # noqa: E731

    p("\n=== Lloydk Airgap Bundle - Pre-flight Checklist ===")
    p(f"Bundle      : {manifest.bundle_name} v{manifest.version}")
    p(f"Target env  : {manifest.target_env}")
    p(f"Git commit  : {manifest.git_commit}")
    p(f"Mode        : {'DRY-RUN (no downloads)' if manifest.dry_run else 'BUILD'}")
    p(f"Est. size   : {manifest.estimated_size_gb} GB")

    p(f"\n[Components: {len(manifest.components)}]")
    for svc, c in manifest.components.items():
        p(f"  - {svc:14s}  {c.image}")

    p(f"\n[Models: {len(manifest.models)}]")
    for m in manifest.models:
        dim = f"dim={m.dim}" if m.dim else "-"
        p(f"  - [{m.role:22s}] {m.name}  ({m.license}, {dim})")

    obs = manifest.observability_images
    p(f"\n[Observability: {len(obs)} images {'(bundled)' if obs else '(SKIPPED - no safety-alert consumer)'}]")
    for img in obs:
        p(f"  - {img}")

    p("\n[Files expected]")
    for f in manifest.files_expected:
        p(f"  - {f}")

    if manifest.dry_run:
        p("\n* DRY-RUN: no docker save / huggingface download / pip download executed.")
        p("* Use without --dry-run on an external network host to build the actual bundle.")


# ─────────────────────────────────────────────────────────────
# 실 빌드
# ─────────────────────────────────────────────────────────────


def _docker_save(image: str, dest: Path) -> bool:
    """docker save → tar. 이미지 미존재 시 pull 시도."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  [skip] already exists: {dest.name}", file=sys.stderr)
        return True
    # 로컬 이미지 존재 확인
    check = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        print(f"  [pull] {image}", file=sys.stderr)
        pull = subprocess.run(["docker", "pull", image], capture_output=True, text=True)
        if pull.returncode != 0:
            print(f"  [WARN] pull failed: {image}\n{pull.stderr[-200:]}", file=sys.stderr)
            return False
    print(f"  [save] {image} -> {dest.name}", file=sys.stderr)
    r = subprocess.run(
        ["docker", "save", "-o", str(dest), image],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  [ERR] docker save failed: {r.stderr[-200:]}", file=sys.stderr)
        return False
    size_mb = dest.stat().st_size / 1_048_576
    print(f"  [ok]   {dest.name}  {size_mb:.0f}MB", file=sys.stderr)
    return True


# 호스트 오프라인 설치 대상에서 제외할 패키지 접두어. torch/nvidia/triton 은 CUDA·플랫폼
# 특정이고 이미지에 구워지므로 호스트 pip 설치 목록(_requirements_no_torch.txt)에서 뺀다.
_HOST_EXCLUDE_PREFIXES = ("torch", "nvidia", "triton")


def _pip_download(out_dir: Path) -> bool:
    """pip download -r requirements → wheels/.

    fail-closed: pip download 가 일부라도 실패하면(rc!=0) False 를 반환해 빌드를 중단한다.
    누락 wheel 을 성공으로 넘기면 고객사 폐쇄망 오프라인 설치가 런타임 ImportError 로 죽는다.
    또한 install.sh·expected_files 가 참조하는 `_requirements_no_torch.txt` 를 반드시 생성한다
    (과거 writer=_requirements_freeze.txt vs consumer=_requirements_no_torch.txt 파일명 불일치 버그).
    """
    wheels_dir = out_dir / "wheels"
    wheels_dir.mkdir(parents=True, exist_ok=True)
    req = _REPO_ROOT / "requirements.txt"
    if not req.exists():
        # pyproject.toml 기반 프로젝트 — pip freeze, git+file 의존성 제외
        print("  [pip] no requirements.txt, exporting from venv (skip editable/git deps)", file=sys.stderr)
        r = subprocess.run(
            [sys.executable, "-m", "pip", "freeze", "--exclude-editable"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  [ERR] pip freeze 실패: {r.stderr[-300:]}", file=sys.stderr)
            return False
        # git+ / file: / @ file: 라인 제거 (폐쇄망 번들에 포함 불가)
        lines = [
            line for line in r.stdout.splitlines()
            if line.strip()
            and not line.startswith("git+") and not line.startswith("-e ") and "@ file:" not in line
        ]
        req = out_dir / "_requirements_freeze.txt"
        req.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 최종 요구 목록(주석·빈 줄 제외).
    requested = [
        ln for ln in req.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    # 호스트 설치용(torch류 제외) 목록 — install.sh 가 이 파일명을 참조하므로 항상 생성.
    no_torch = [
        ln for ln in requested
        if not ln.split("==")[0].split(">")[0].split("<")[0].strip().lower().startswith(_HOST_EXCLUDE_PREFIXES)
    ]
    (out_dir / "_requirements_no_torch.txt").write_text("\n".join(no_torch) + "\n", encoding="utf-8")

    r = subprocess.run(
        [sys.executable, "-m", "pip", "download", "-r", str(req),
         "-d", str(wheels_dir), "--no-deps", "-q"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  [ERR] pip download 실패(rc={r.returncode}): {r.stderr[-300:]}", file=sys.stderr)
        return False
    # --no-deps 는 요구 1건당 산출 1건(.whl 또는 sdist .tar.gz/.zip). 산출이 요구보다 적으면
    # 불완전 번들 — 자기검사로 표면화한다.
    downloaded = sum(
        1 for f in wheels_dir.iterdir()
        if f.suffix in (".whl", ".zip") or f.name.endswith(".tar.gz")
    )
    print(f"  [pip]  {downloaded} dists -> {wheels_dir} (requested {len(requested)})", file=sys.stderr)
    if downloaded < len(requested):
        print(
            f"  [ERR] wheel 자기검사 실패: 요구 {len(requested)} > 산출 {downloaded}. "
            "불완전 번들 — 빌드 중단(fail-closed).",
            file=sys.stderr,
        )
        return False
    return True


def _copy_ocr_binaries(out_dir: Path) -> None:
    """Tesseract + kor.traineddata + poppler 바이너리를 번들에 포함.

    폐쇄망 배포 시 pip wheels 만으로는 OCR이 동작하지 않음.
    시스템 바이너리를 ocr-binaries/ 디렉터리에 복사해 두고 install.sh 가 설치.

    Windows 번들 빌더 기준 경로:
      Tesseract: C:/Program Files/Tesseract-OCR/ 또는 환경변수 TESSERACT_DIR
      poppler  : ~/tools/poppler/bin/ 또는 환경변수 POPPLER_DIR
    """
    import shutil
    import os

    ocr_dir = out_dir / "ocr-binaries"

    # ── Tesseract ──────────────────────────────────────────────────
    tess_src_candidates = [
        os.environ.get("TESSERACT_DIR", ""),
        r"C:\Program Files\Tesseract-OCR",
    ]
    tess_src = next((Path(p) for p in tess_src_candidates if p and Path(p).exists()), None)
    if tess_src:
        tess_dst = ocr_dir / "tesseract"
        tess_dst.mkdir(parents=True, exist_ok=True)
        # 실행파일만 (전체 설치본은 너무 크므로 exe + dll)
        for pattern in ("tesseract.exe", "*.dll"):
            for f in tess_src.glob(pattern):
                shutil.copy2(f, tess_dst / f.name)
        # kor.traineddata
        tessdata_src = tess_src / "tessdata"
        if tessdata_src.exists():
            tessdata_dst = ocr_dir / "tessdata"
            tessdata_dst.mkdir(exist_ok=True)
            for lang in ("kor.traineddata", "eng.traineddata"):
                src_f = tessdata_src / lang
                if src_f.exists():
                    shutil.copy2(src_f, tessdata_dst / lang)
        print(f"  [ocr]  Tesseract 바이너리 → {tess_dst}", file=sys.stderr)
    else:
        print("  [WARN] Tesseract 미발견 — TESSERACT_DIR 환경변수로 경로 지정 필요", file=sys.stderr)

    # ── poppler ────────────────────────────────────────────────────
    poppler_src_candidates = [
        os.environ.get("POPPLER_DIR", ""),
        str(Path.home() / "tools" / "poppler" / "bin"),
        r"C:\Program Files\poppler\bin",
    ]
    poppler_src = next((Path(p) for p in poppler_src_candidates if p and (Path(p) / "pdftoppm.exe").exists()), None)
    if poppler_src:
        poppler_dst = ocr_dir / "poppler" / "bin"
        poppler_dst.mkdir(parents=True, exist_ok=True)
        for f in poppler_src.glob("*"):
            if f.is_file():
                shutil.copy2(f, poppler_dst / f.name)
        print(f"  [ocr]  poppler 바이너리 → {poppler_dst}", file=sys.stderr)
    else:
        print("  [WARN] poppler 미발견 — POPPLER_DIR 환경변수로 경로 지정 필요", file=sys.stderr)


def _copy_infra(out_dir: Path) -> None:
    """docker-compose(airgap 포함), env template, alembic, OCR 바이너리 복사. (ES 설정 폐기 — §03)"""
    import shutil

    infra = out_dir / "infra-config"
    infra.mkdir(parents=True, exist_ok=True)

    for src, dst in [
        (_REPO_ROOT / "docker-compose.yml",              infra / "docker-compose.yml"),
        # 폐쇄망 전용 compose (image 참조·beat 서비스·named 볼륨) — 운영 배포는 이걸 사용.
        (_REPO_ROOT / "docker-compose.airgap.yml",       infra / "docker-compose.airgap.yml"),
    ]:
        if src.exists():
            shutil.copy2(src, dst)

    # docs — INSTALL 절차서 등 (manifest files_expected의 docs/INSTALL.md 충족)
    docs_dst = out_dir / "docs"
    docs_dst.mkdir(parents=True, exist_ok=True)
    install_md = _REPO_ROOT / "docs" / "INSTALL.md"
    if install_md.exists():
        shutil.copy2(install_md, docs_dst / "INSTALL.md")

    # .env template
    env_template = infra / ".env.template"
    env_example = _REPO_ROOT / ".env.example"
    if env_example.exists():
        import shutil as _sh
        _sh.copy2(env_example, env_template)
    else:
        env_template.write_text(
            "# Copy this to .env and fill in values\n"
            "DATABASE_URL=postgresql://lloydk:lloydk_dev@postgres:5432/lloydk\n"
            "VECTOR_BACKEND=pg\n"
            "REDIS_URL=redis://redis:6379/0\n"
            "STORAGE_BACKEND=local\n"   # 폐쇄망=로컬FS(file://). MinIO 미사용.
            "LLM_PROVIDER=vllm\n"
            "DEPLOY_PROFILE=onprem-local\n",
            encoding="utf-8",
        )

    # alembic migrations
    alembic_src = _REPO_ROOT / "alembic"
    alembic_dst = out_dir / "db-migrations" / "alembic"
    if alembic_src.exists() and not alembic_dst.exists():
        import shutil as _sh
        _sh.copytree(alembic_src, alembic_dst)

    # ── OCR 시스템 바이너리 번들 ──────────────────────────────────
    # Tesseract 실행파일 + kor.traineddata, poppler 바이너리를 번들에 포함.
    # 폐쇄망에서 OCR 동작 필수 — pip wheels 만으로는 부족.
    _copy_ocr_binaries(out_dir)

    # install.sh
    install_sh = out_dir / "install.sh"
    install_sh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "BUNDLE_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n\n"
        "echo '=== Lloydk Airgap Bundle Install ==='\n"
        "# 1) docker load\n"
        "for tar in \"$BUNDLE_DIR/docker-images\"/*.tar; do\n"
        "  echo \"Loading $tar ...\"\n"
        "  docker load -i \"$tar\"\n"
        "done\n\n"
        "# 2) (옵션) 호스트 파이썬 deps — 컨테이너 배포는 deps 가 이미지에 포함(INSTALL.md).\n"
        "#    pip 이 있으면 fail-closed 로 설치(오류 시 set -e 로 스크립트 중단), 없으면 명시적 skip.\n"
        "#    과거의 `|| true` 는 누락 wheel 을 조용히 성공 처리해 런타임 ImportError 로 이어졌다.\n"
        "REQ=\"$BUNDLE_DIR/python-deps/_requirements_no_torch.txt\"\n"
        "if command -v pip >/dev/null 2>&1 && [ -f \"$REQ\" ]; then\n"
        "  echo '[deps] host python deps 설치 ...'\n"
        "  pip install --no-index --find-links=\"$BUNDLE_DIR/python-deps/wheels\" -r \"$REQ\"\n"
        "else\n"
        "  echo '[deps] host python deps skip (pip 미탑재 또는 컨테이너 전용 배포 — deps 는 이미지에 포함)'\n"
        "fi\n\n"
        "# 3) OCR 바이너리 설치 (Linux 온프레미스 기준)\n"
        "if [ -d \"$BUNDLE_DIR/ocr-binaries/tesseract\" ]; then\n"
        "  echo '[OCR] Tesseract 설치 중 ...'\n"
        "  dpkg -i \"$BUNDLE_DIR/ocr-binaries/tesseract\"/*.deb 2>/dev/null || \\\n"
        "    cp -r \"$BUNDLE_DIR/ocr-binaries/tesseract/bin/\" /usr/local/bin/ || true\n"
        "  cp \"$BUNDLE_DIR/ocr-binaries/tessdata/kor.traineddata\" \\\n"
        "     /usr/share/tesseract-ocr/5/tessdata/ 2>/dev/null || \\\n"
        "     mkdir -p /usr/local/share/tessdata && \\\n"
        "     cp \"$BUNDLE_DIR/ocr-binaries/tessdata/kor.traineddata\" \\\n"
        "        /usr/local/share/tessdata/ || true\n"
        "fi\n"
        "if [ -d \"$BUNDLE_DIR/ocr-binaries/poppler\" ]; then\n"
        "  echo '[OCR] poppler 설치 중 ...'\n"
        "  cp -r \"$BUNDLE_DIR/ocr-binaries/poppler/bin/\"* /usr/local/bin/ || true\n"
        "fi\n\n"
        "# 4) env 설정\n"
        "[ -f .env ] || cp \"$BUNDLE_DIR/infra-config/.env.template\" .env\n"
        "echo 'Next: edit .env, then follow docs/INSTALL.md:'\n"
        "echo '  docker compose --env-file .env -f infra-config/docker-compose.airgap.yml up -d'\n",
        encoding="utf-8",
    )
    install_sh.chmod(0o755)

    # verify.sh
    verify_sh = out_dir / "verify.sh"
    verify_sh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo '=== Bundle Verify ==='\n"
        "cd \"$(dirname \"$0\")\"\n"
        "sha256sum -c CHECKSUMS.sha256 && echo 'Checksums OK' || echo 'CHECKSUM MISMATCH'\n",
        encoding="utf-8",
    )
    verify_sh.chmod(0o755)


def _copy_observability(out_dir: Path) -> None:
    """관측성 스택 설정(prometheus·alert_rules·grafana·loki·airgap overlay compose)을 번들에 복사.

    이미지 tar 는 build_bundle 이 best-effort 로 별도 저장. 설정은 항상 복사 — 이 파일들의
    부재가 '관측성 미배선' 갭의 절반(고객사가 무엇을 어떻게 띄우는지 모름)이었다.
    """
    import shutil  # noqa: PLC0415

    src = _REPO_ROOT / "infra" / "observability"
    if not src.exists():
        print("  [WARN] infra/observability 없음 — 관측성 설정 미동봉", file=sys.stderr)
        return
    dst = out_dir / "observability"
    if dst.exists():
        print(f"  [skip] already exists: {dst}", file=sys.stderr)
        return
    # dev overlay(lloydk-poc 네트워크)는 폐쇄망에서 무용이므로 제외하고 나머지 전부 복사.
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("docker-compose.observability.yml"),
    )
    print(f"  [obs]  관측성 설정 → {dst}", file=sys.stderr)


def _copy_trained_classifier(manifest: "BundleManifest", out_dir: Path) -> bool:
    """학습된 분류기 디렉토리(가중치+config+temperature.json)를 models/classifier-trained로 복사.

    manifest.models에 role=classifier_trained 항목이 없으면(=학습 모델 미지정) 경고 후
    True 반환(베이스 모델만 번들 — 빌드 자체는 실패 아님). 지정됐는데 복사 실패 시 False.
    """
    import shutil  # noqa: PLC0415

    trained = next((m for m in manifest.models if m.role == "classifier_trained"), None)
    if trained is None or not trained.source_path:
        print(
            "  [WARN] 학습 분류기 미지정 — 베이스 모델만 번들됩니다(폐쇄망에서 rule-fallback). "
            "--classifier-model-dir 또는 CLASSIFIER_MODEL_DIR로 학습 가중치를 지정하세요(#40).",
            file=sys.stderr,
        )
        return True
    src = Path(trained.source_path)
    if not src.exists():
        print(f"  [ERR] classifier model dir 없음: {src}", file=sys.stderr)
        return False
    dst = out_dir / "models" / "classifier-trained"
    if dst.exists():
        print(f"  [skip] already exists: {dst}", file=sys.stderr)
        return True
    shutil.copytree(src, dst)
    has_temp = (dst / "temperature.json").exists()
    print(
        f"  [model] classifier-trained -> {dst}  (temperature.json: {'포함' if has_temp else '없음'})",
        file=sys.stderr,
    )
    return True


def build_bundle(manifest: "BundleManifest", out_dir: Path) -> int:
    """실 빌드: docker save + pip download + infra 파일 복사."""
    print("\n=== Lloydk Airgap Bundle - BUILD ===", file=sys.stderr)
    images_dir = out_dir / "docker-images"
    images_dir.mkdir(parents=True, exist_ok=True)

    failed: list[str] = []

    # docker save
    for svc, entry in manifest.components.items():
        ok = _docker_save(entry.image, images_dir / f"{svc}.tar")
        if not ok:
            failed.append(f"docker:{svc}")

    # pip download
    pip_dir = out_dir / "python-deps"
    pip_dir.mkdir(parents=True, exist_ok=True)
    if not _pip_download(pip_dir):
        failed.append("pip-download")

    # infra 파일
    _copy_infra(out_dir)

    # 관측성 스택 — 설정은 항상 복사, 이미지는 best-effort(실패해도 빌드 중단 아님).
    # 관측성은 핵심 기동 요소가 아니므로 fail-visible(경고)로 두되, 무엇이 빠졌는지 반드시 알린다.
    if manifest.observability_images:
        _copy_observability(out_dir)
        obs_failed: list[str] = []
        for img in manifest.observability_images:
            tar = images_dir / f"obs-{_obs_tar_name(img)}.tar"
            if not _docker_save(img, tar):
                obs_failed.append(img)
        if obs_failed:
            print(
                f"\n[bundle][WARN] 관측성 이미지 미저장: {obs_failed}. 번들은 완성되나 이 이미지가 없으면 "
                "고객사 폐쇄망에서 Prometheus/Grafana 로 안전 알림을 소비할 수 없습니다. "
                "외부망 빌드호스트에서 해당 이미지를 pull 후 재빌드하거나 수동 동봉하세요.",
                file=sys.stderr,
            )

    # #40: 학습된 분류기 모델(가중치 + temperature.json) 복사
    if not _copy_trained_classifier(manifest, out_dir):
        failed.append("classifier-trained")

    if failed:
        print(f"\n[bundle] partial build — failed: {failed}", file=sys.stderr)
        print("[bundle] run verify.sh after deploying to confirm checksums", file=sys.stderr)
        return 1

    print("\n[bundle] all components saved OK", file=sys.stderr)
    return 0


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="번들 버전 (예: 1.0.0)")
    ap.add_argument("--output", default="dist/lloydk-airgap-bundle", help="출력 디렉터리")
    ap.add_argument("--target-env", default="KOIPA-prod")
    ap.add_argument("--dry-run", action="store_true", help="다운로드 없이 manifest만 생성")
    ap.add_argument(
        # 폐쇄망 번들은 airgap compose 를 파싱한다(image: 태그 보유 → api/worker 이미지 추출 가능).
        # dev compose(docker-compose.yml)는 api/worker 가 build: 라 image 추출 불가 + minio/mlflow 잔존.
        "--compose", default=str(_REPO_ROOT / "docker-compose.airgap.yml"),
        help="파싱할 docker-compose 경로(기본: airgap)",
    )
    ap.add_argument(
        "--config", default=str(_REPO_ROOT / "src" / "lloydk" / "config.py"),
        help="파싱할 config.py 경로",
    )
    ap.add_argument(
        "--classifier-model-dir", default=None,
        help="학습된 분류기 가중치 디렉토리(가중치+temperature.json). "
             "미지정 시 env CLASSIFIER_MODEL_DIR/LLOYDK_CLASSIFIER_MODEL_DIR 사용(#40)",
    )
    ap.add_argument(
        "--skip-observability", action="store_true",
        help="관측성 스택(Prometheus/Grafana/Loki + 안전 알림)을 번들에서 제외. "
             "기본은 포함 — 안전 알림 소비자는 폐쇄망 운영 필수.",
    )
    args = ap.parse_args()

    classifier_model_dir = resolve_classifier_model_dir(args.classifier_model_dir)

    manifest = build_manifest(
        version=args.version,
        target_env=args.target_env,
        dry_run=args.dry_run,
        compose_path=Path(args.compose),
        config_path=Path(args.config),
        classifier_model_dir=classifier_model_dir,
        include_observability=not args.skip_observability,
    )

    # 코어 서비스 이미지 누락 fail-closed — dry-run 에서도 검사해 CI 가 회귀를 잡는다.
    # (인라인 주석/미확장 ${VAR} 로 postgres 등이 통째 빠지면 폐쇄망 기동 불가.)
    missing_core = [s for s in _REQUIRED_CORE_SERVICES if s not in manifest.components]
    if missing_core:
        print(
            f"\n[bundle][FATAL] 폐쇄망 코어 서비스 이미지 누락: {missing_core}. "
            "docker-compose.airgap.yml 의 image: 파싱을 확인하세요(인라인 주석·${VAR} 미확장 등). "
            "이 번들은 고객사 폐쇄망에서 기동 불가합니다.",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(args.output)
    paths = write_manifest(manifest, out_dir)

    if args.dry_run:
        # CHECKSUMS는 manifest 자체만 포함 (실제 컴포넌트 없음)
        write_checksums(out_dir, list(paths.values()))
        print_checklist(manifest)
        print(f"\n[bundle] dry-run OK -> {paths['yaml']}", file=sys.stderr)
        return 0

    # ── 실 빌드 ──────────────────────────────────────────────
    rc = build_bundle(manifest, out_dir)
    if rc == 0:
        all_files = list(out_dir.rglob("*"))
        actual_files = [f for f in all_files if f.is_file()]
        write_checksums(out_dir, actual_files)
        print_checklist(manifest)
        print(f"\n[bundle] build OK -> {out_dir}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

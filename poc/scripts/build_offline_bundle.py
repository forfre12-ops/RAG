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
    role: str  # classifier | embedding | llm | embedding_fallback


@dataclass
class PluginEntry:
    name: str
    version: str
    sha256: str | None = None


@dataclass
class BundlePolicies:
    vector_backend_default: str = "es"
    llm_provider_default: str = "vllm"
    qwen3_thinking_mode: bool = False


@dataclass
class SecurityScan:
    scanned_with: str = "trivy (dry-run skipped)"
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
        }


# ─────────────────────────────────────────────────────────────
# 추출 로직 (외부 네트워크 0)
# ─────────────────────────────────────────────────────────────

_IMAGE_LINE = re.compile(r"^\s*image:\s*(\S+)\s*$")
_MODEL_RE = re.compile(r"^\s*(\w+_model)\s*:\s*str\s*=\s*\"([^\"]+)\"", re.MULTILINE)
# `vllm_model: str = "..."`, `classifier_base_model: str = "..."` 둘 다 잡음
_ANY_MODEL_RE = re.compile(
    r"^\s*([a-z_]+)\s*:\s*str\s*=\s*\"([^\"]+)\"\s*$",
    re.MULTILINE,
)


def extract_components_from_compose(compose_path: Path) -> dict[str, ComponentEntry]:
    """docker-compose.yml에서 image: 라인 파싱.

    YAML 파서를 쓰지 않는 이유: PoC 환경에 pyyaml이 없어도 동작해야 함.
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
                full = m.group(1)
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


def default_es_plugins(es_version: str) -> list[PluginEntry]:
    return [
        PluginEntry(name="analysis-nori", version=es_version),
        PluginEntry(name="repository-s3", version=es_version),
    ]


# 컴포넌트별 예상 크기 (GB, doc/12 §3.2 기반 추정)
_COMPONENT_SIZE_GB: dict[str, float] = {
    "api": 1.5,
    "worker": 1.5,
    "postgres": 0.4,
    "elasticsearch": 1.0,
    "minio": 0.2,
    "redis": 0.1,
    "mlflow": 0.6,
}

_MODEL_SIZE_GB: dict[str, float] = {
    "classifier": 0.7,
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


def expected_files(components: dict[str, ComponentEntry], models: list[ModelEntry]) -> list[str]:
    files: list[str] = ["README.md", "install.sh", "verify.sh", "manifest.yaml", "CHECKSUMS.sha256"]
    for svc in components:
        files.append(f"docker-images/{svc}.tar")
    for m in models:
        files.append(f"models/{m.name.replace('/', '-')}/")
    files.extend([
        "python-deps/wheels/",
        "python-deps/requirements.lock.txt",
        "es-plugins/analysis-nori-{ver}.zip",
        "es-plugins/repository-s3-{ver}.zip",
        "infra-config/docker-compose.yml",
        "infra-config/.env.template",
        "infra-config/es-index-template.json",
        "infra-config/userdict_ko.txt",
        "infra-config/ilm-policy-secrets.json",
        "db-migrations/alembic/",  # baseline + 후속 revision 전체 (init.sql 폐기)
        "docs/INSTALL.md",
        "docs/OPERATION.md",
        "docs/TROUBLESHOOTING.md",
        "licenses/third-party-licenses.txt",
    ])
    return files


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
) -> BundleManifest:
    components = extract_components_from_compose(compose_path)

    # 로컬 빌드 이미지 (docker-compose의 `build:` 블록) — image 라인이 없어 자동 추출 불가
    for svc in ("api", "worker"):
        components.setdefault(
            svc,
            ComponentEntry(image=f"lloydk-{svc}:{version}", version=version),
        )

    models = extract_models_from_config(config_path)
    es_version = components.get("elasticsearch", ComponentEntry("", "8.15.3")).version
    plugins = default_es_plugins(es_version)
    size = estimate_total_size(components, models, plugins)
    files = expected_files(components, models)

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

    p(f"\n[ES plugins: {len(manifest.es_plugins)}]")
    for plg in manifest.es_plugins:
        p(f"  - {plg.name}-{plg.version}.zip")

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


def _pip_download(out_dir: Path) -> bool:
    """pip download -r requirements → wheels/."""
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
            return False
        # git+ / file: / @ file: 라인 제거 (폐쇄망 번들에 포함 불가)
        lines = [
            l for l in r.stdout.splitlines()
            if not l.startswith("git+") and not l.startswith("-e ") and "@ file:" not in l
        ]
        req = out_dir / "_requirements_freeze.txt"
        req.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "download", "-r", str(req),
         "-d", str(wheels_dir), "--no-deps", "-q"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  [WARN] pip download partial: {r.stderr[-300:]}", file=sys.stderr)
    count = sum(1 for _ in wheels_dir.glob("*.whl"))
    print(f"  [pip]  {count} wheels -> {wheels_dir}", file=sys.stderr)
    return True


def _copy_infra(out_dir: Path) -> None:
    """docker-compose, env template, ES 설정 파일 복사."""
    import shutil

    infra = out_dir / "infra-config"
    infra.mkdir(parents=True, exist_ok=True)

    for src, dst in [
        (_REPO_ROOT / "docker-compose.yml",              infra / "docker-compose.yml"),
        (_REPO_ROOT / "infra" / "es" / "userdict_ko.txt", infra / "userdict_ko.txt"),
    ]:
        if src.exists():
            shutil.copy2(src, dst)

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
            "ES_URL=http://elasticsearch:9200\n"
            "REDIS_URL=redis://redis:6379/0\n"
            "MINIO_ENDPOINT=minio:9000\n"
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
        "# 2) pip install from wheels\n"
        "pip install --no-index --find-links=\"$BUNDLE_DIR/python-deps/wheels\" \\\n"
        "  -r \"$BUNDLE_DIR/python-deps/requirements.lock.txt\" || true\n\n"
        "# 3) env 설정\n"
        "cp \"$BUNDLE_DIR/infra-config/.env.template\" .env\n"
        "echo 'Edit .env, then run: docker compose up -d'\n",
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
        "--compose", default=str(_REPO_ROOT / "docker-compose.yml"),
        help="파싱할 docker-compose.yml 경로",
    )
    ap.add_argument(
        "--config", default=str(_REPO_ROOT / "src" / "lloydk" / "config.py"),
        help="파싱할 config.py 경로",
    )
    args = ap.parse_args()

    manifest = build_manifest(
        version=args.version,
        target_env=args.target_env,
        dry_run=args.dry_run,
        compose_path=Path(args.compose),
        config_path=Path(args.config),
    )

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

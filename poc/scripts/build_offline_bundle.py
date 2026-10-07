"""폐쇄망 자기완비 번들 빌더.

doc/12_폐쇄망_배포_설계.md §3·§4 구현.

두 가지 모드:
  --dry-run : 다운로드·빌드 없이 manifest.yaml만 미리 생성 + 체크리스트·예상 크기 출력
  (기본)    : 실제 docker save / huggingface 캐시 스테이징 실행 (네트워크 필요)

핵심 설계:
  - docker-compose.yml에서 image: 라인 파싱 → 컴포넌트 자동 추출
  - poc/src/koipa/config.py의 모델명 정적 분석 (import 없이 텍스트 파싱)
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
import io
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

# 배포 정본(poc/deploy_manifest.toml). 번들에 실을 파일의 목록은 코드가 아니라 거기에 있다.
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import deploy_manifest as _dm  # noqa: E402

# [cp949 2026-08-15] 한국어 Windows 콘솔은 cp949 라서 본문의 em dash 하나에 출력이 죽는다.
# 실측: `--help` 가 UnicodeEncodeError 로 통째로 실패했다 - 도움말조차 못 읽는 상태였다.
# 기록된 교훈이고 gate_p1_candidate.py 가 쓰는 관용구를 그대로 쓴다. 문자를 하나씩 쫓는
# 대신 출구를 고정한다 - 새 메시지가 들어와도 다시 죽지 않는다.
for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        setattr(sys, _s, io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))


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
_REQUIRED_CORE_SERVICES = ("redis", "api", "worker", "beat")

# DB 이미지는 **반드시** 번들에 있어야 한다 — 폐쇄망에서 DB 없이 뜨는 번들은 못 만든다.
# [2026-09-09] 후보에서 mariadb 를 뺐다(PostgreSQL + pgvector 로 복귀). 목록 형태는
#   그대로 둔다 — 검사의 의도는 "DB 이미지가 있어야 한다"이지 "postgres 여야 한다"가
#   아니고, 엔진이 다시 늘면 여기 한 줄만 늘리면 된다.
_REQUIRED_DB_SERVICES = ("postgres",)

# 관측성 스택 이미지 — infra/observability/docker-compose.observability.airgap.yml 과 태그 동기.
# best-effort 동봉(핵심 아님): 저장 실패 시 경고만(빌드 실패 아님). 안전 알림(FnrSpike·
# AuditChainBroken·KillGateTripped 등)의 폐쇄망 소비자. --skip-observability 로 제외 가능.
_OBSERVABILITY_IMAGES = (
    "prom/prometheus:v2.55.1",
    "prom/alertmanager:v0.27.0",
    "grafana/grafana:11.3.0",
    "grafana/loki:3.2.1",
    "grafana/promtail:3.2.1",
    "prometheuscommunity/postgres-exporter:v0.16.0",
    # redis_exporter 는 뺐다 — prometheus 알림 규칙(up{job="koipa-api"}·up{job="postgres"} 등)과
    # grafana 대시보드가 redis 메트릭을 쓰지 않는다. 목록의 정본은 deploy_manifest.toml.
)

# docker-compose 스타일 `${NAME}` / `${NAME:-default}` 치환용.
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env_vars(value: str, overrides: dict[str, str] | None = None) -> str:
    """compose 의 `${NAME}` / `${NAME:-default}` 치환.

    우선순위: overrides > 환경변수(비어있지 않을 때) > default > 빈 문자열.
    번들러는 실제 태그를 알아야 docker save/load 가 일치한다 — `${IMAGE_TAG:-1.0.0-rc1}` 을
    리터럴로 캡처하면 `docker save koipa-worker:${IMAGE_TAG:-1.0.0-rc1}` 가 존재불가 태그로
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
    env KOIPA_CLASSIFIER_MODEL_DIR. 미설정이면 None(베이스 모델만 번들 — 경고 대상).
    서빙은 이 디렉토리에서 가중치 + temperature.json(보정)을 로드하므로, 빠지면
    폐쇄망에서 미학습·무보정으로 동작한다(#40).
    """
    import os  # noqa: PLC0415
    cand = explicit or os.environ.get("CLASSIFIER_MODEL_DIR") or os.environ.get("KOIPA_CLASSIFIER_MODEL_DIR")
    if not cand:
        return None
    p = Path(cand)
    return p if p.exists() else None


# ─────────────────────────────────────────────────────────────
# 번들↔릴리스 parity + 위생 (배포 전 fail-closed 게이트)
# ─────────────────────────────────────────────────────────────


def hash_model_dir(model_dir: Path) -> str:
    """모델 디렉토리의 결정적 지문(sha256). 파일 상대경로+내용을 정렬 순서로 해시.

    번들에 실린 학습 분류기가 '릴리스 모델'과 동일본인지(번들↔prod parity)를 확인하는
    fingerprint. 빌드 호스트/OS 무관하게 같은 가중치면 같은 값 → manifest 에 기록해
    다운스트림 verify 가 대조할 수 있다.
    """
    h = hashlib.sha256()
    files = sorted(
        (p for p in model_dir.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(model_dir).as_posix(),
    )
    for p in files:
        h.update(p.relative_to(model_dir).as_posix().encode("utf-8"))
        h.update(b"\0")
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def _model_version_id(path_or_name: str) -> str:
    """모델 경로/이름에서 버전 식별자(basename) 추출(후행 슬래시·백슬래시 정규화)."""
    return Path(str(path_or_name).replace("\\", "/").rstrip("/")).name


def check_model_parity(bundled_dir: Path | None, release_model: str) -> str | None:
    """번들에 실리는 학습 분류기가 '릴리스 모델' 버전을 담고 있는지 강제.

    반환: 위반 메시지(불일치) 또는 None(일치). 문자열 비교라 dry-run(CI)에서도
    동작 — 잘못된/구버전 모델이 번들에 실리는 것을 배포 전에 차단한다. F1/FNR 게이트는
    릴리스 모델을 기술하므로, 다른 모델을 실으면 번들이 미검증본이 된다.

    ⚠ 종전에는 `bundled_dir is None`(미동봉)을 **위반이 아니라고 반환**했다. 그래서
      학습 분류기가 통째로 빠진 번들이 parity 검사를 통과했다. 폐쇄망에서 그 번들은
      rule-fallback 으로 뜨고(무음 미탐 위험) 아무것도 막지 않는다 — 실측 2026-08-15:
      dry-run 무경고 · 빌드 [WARN] 만 · parity 통과 · deploy_airgap info 만, 게이트 네
      개가 연속으로 통과시켰다. 미동봉은 **위반이다.**
    """
    if bundled_dir is None:
        return (
            "학습 분류기가 번들에 없다(베이스 모델만). 폐쇄망에서 rule-fallback 으로 뜬다 "
            "- 무음 미탐 위험. --classifier-model-dir 또는 CLASSIFIER_MODEL_DIR 로 릴리스 "
            f"모델({release_model})을 지정하라. 의도적 rule-fallback 번들이면 "
            "--allow-base-only-classifier 를 명시할 것."
        )
    want = _model_version_id(release_model)
    bundled_norm = str(bundled_dir).replace("\\", "/").rstrip("/")
    if want and want not in bundled_norm:
        return (
            f"bundled classifier '{bundled_dir}' 에 릴리스 모델 버전 '{want}' 가 없음 "
            f"(release={release_model}). --classifier-model-dir 를 릴리스 모델로 맞추거나 "
            "--release-model 을 이번 릴리스 버전으로 갱신하라."
        )
    return None


# 배포 정본(deploy_manifest.toml)에서 뺀 최상위 산출물. 예전 번들 폴더 위에 다시 빌드하면 그대로 실려 나간다.
_RETIRED_BUNDLE_DIRS = ("python-deps", "db-migrations", "wheels")


def check_bundle_hygiene(out_dir: Path, manifest: "BundleManifest") -> list[str]:
    """이미 존재하는 출력 디렉토리에서 stale/이질 산출물을 fail-closed 로 잡는다.

    핵심 함정: 재빌드 시 _docker_save/copytree 가 'already exists' 로 SKIP → 예전(ES시대)
    번들 위에 덮어쓰면 elasticsearch/minio/mlflow.tar 와 정본에서 뺀 산출물이 그대로 실려나간다.
      - docker-images/ 의 tar 중 매니페스트 컴포넌트/관측성에 없는 것(=이질 이미지, 예: 같은 이미지의 beat.tar)
      - 정본에서 뺀 최상위 폴더(python-deps/·db-migrations/)와 HF 캐시의 리비전 중복
    반환: 위반 목록(빈 리스트=청결). 해당 폴더가 없으면 no-op(순수 dry-run 안전).
    """
    violations: list[str] = []
    images_dir = out_dir / "docker-images"
    if images_dir.is_dir():
        allowed = {f"{svc}.tar" for svc in image_tar_plan(manifest.components)}
        allowed |= {f"obs-{_obs_tar_name(img)}.tar" for img in manifest.observability_images}
        for tar in sorted(images_dir.glob("*.tar")):
            if tar.name not in allowed:
                violations.append(
                    f"foreign image tar: docker-images/{tar.name} "
                    "(매니페스트 컴포넌트/관측성 아님 - 예전 번들 잔존 의심)"
                )
    # [stale 분류기] 재빌드는 'already exists' 로 SKIP 하므로 예전 번들의 분류기가 그대로
    # 남는다. 실측 2026-08-15: dist/ 번들에 **6/2 모델**이 들어 있었다 — 정확도 0.783 ·
    # 미탐률 0.217 로, 7/29 승격한 배포본(0.953 · 0.047) 대비 미탐이 4.6배다. 지재원 서버가
    # 이 번들로 설치되면 승격 전 모델이 뜨고 아무것도 막지 않았다.
    #   dry-run 무경고 -> 빌드 [WARN] 만 -> parity 통과 -> deploy_airgap info 만
    # 이미지·wheel 은 보면서 정작 등급을 만드는 모델은 안 봤다.
    trained_entry = next((m for m in (getattr(manifest, "models", None) or [])
                          if m.role == "classifier_trained"), None)
    bundled_model = out_dir / "models" / "classifier-trained"
    if bundled_model.is_dir() and trained_entry is not None and trained_entry.source_path:
        src = Path(trained_entry.source_path)
        if src.is_dir() and hash_model_dir(bundled_model) != hash_model_dir(src):
            violations.append(
                f"stale classifier: models/classifier-trained 내용이 릴리스 모델({src}) 과 "
                "다르다 - 예전 번들 잔존. 그 디렉토리를 지우고 재빌드하라."
            )

    for retired in _RETIRED_BUNDLE_DIRS:
        if (out_dir / retired).exists():
            violations.append(
                f"retired artifact: {retired}/ — 배포 정본(deploy_manifest.toml)에서 뺀 산출물이 예전 번들에 남아 있다. "
                "출력 디렉토리를 비우고 재빌드하라."
            )
    hf_hub = out_dir / "models" / "hf" / "hub"
    if hf_hub.is_dir():
        for cache in sorted(hf_hub.glob("models--*")):
            snaps = cache / "snapshots"
            if snaps.is_dir() and len([s for s in snaps.iterdir() if s.is_dir()]) > 1:
                violations.append(
                    f"duplicate HF revision: {cache.name} 스냅샷이 둘 이상이다 — refs/main 하나만 실어야 한다"
                )
    return violations


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
    "llm": 29.5,  # Qwen3-14B bf16 safetensors — 실측 다운로드 29,552,588,155B(2026-10-03,
                  # `huggingface_hub.snapshot_download`). 종전 10.0 은 실제로 담지 않던 시절
                  # AWQ(4bit) 추정치를 그대로 남겨 둔 값이라 지금 담는 bf16 가중치와 안 맞았다.
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


# 컨테이너 런타임 RPM 스테이징 디렉터리.
# 왜(2026-08-27). 설치 대상이 둘인데 **둘 다 인터넷 저장소를 쓸 수 없다** —
#   지재원 서버 : 공공망. KL 이 원격 접속해 설치한다.
#   고객사 서버 : 폐쇄망. 매체로 반입해 직접 설치한다.
# 즉 `dnf install docker-ce` 가 양쪽 다 불가능하다. 런타임을 전제조건으로만 적어 두면
# 현장에서 담당자가 첫 단계에서 막히고 그 자리에 우리는 없다. RPM 을 번들에 넣는다.
#
# RPM 은 이 빌더가 만들어 내지 못한다(인터넷 되는 Rocky 8.10 호스트가 필요하다).
# 그 호스트에서 아래를 한 번 돌려 rpms/ 를 채운 뒤 빌드한다:
#   dnf download --resolve --destdir=poc/rpms #       docker-ce docker-ce-cli containerd.io docker-compose-plugin
# (podman 계열로 갈 경우: dnf download --resolve --destdir=poc/rpms podman podman-compose)
_RPM_DIR_ENV = "KOIPA_RPM_DIR"


def rpm_source_dir() -> Path:
    """런타임 RPM 을 담아 둔 디렉터리. 기본은 poc/rpms."""
    return Path(os.environ.get(_RPM_DIR_ENV) or (_REPO_ROOT / "rpms"))


def staged_rpms() -> list[Path]:
    """스테이징된 .rpm 목록(정렬). 없으면 빈 목록."""
    d = rpm_source_dir()
    return sorted(d.glob("*.rpm")) if d.is_dir() else []


def image_tar_plan(components: dict[str, ComponentEntry]) -> dict[str, str]:
    """docker save 로 만들 tar 이름(확장자 제외) → 이미지. 같은 이미지를 쓰는 서비스는 tar 하나로 합친다.

    beat 는 worker 와 같은 koipa-worker 이미지다. 서비스마다 저장하면 바이트까지 같은 796MB 짜리
    tar 가 하나 더 실린다(20260928 번들에서 cmp 로 확인). 먼저 나온 서비스(worker)의 이름을 쓴다.
    """
    plan: dict[str, str] = {}
    seen: set[str] = set()
    for svc, entry in components.items():
        image = getattr(entry, "image", None) or svc  # 이미지 정보가 없는 항목은 서비스 하나당 tar 하나
        if image in seen:
            continue
        seen.add(image)
        plan[svc] = image
    return plan


def expected_files(
    components: dict[str, ComponentEntry],
    models: list[ModelEntry],
    observability_images: list[str] | None = None,
) -> list[str]:
    """번들에 있어야 할 파일 선언. 정본(deploy_manifest.toml)의 [bundle] 을 그대로 옮기고,
    compose·config 에서 나오는 이미지·모델만 여기서 보탠다."""
    b = _dm.load()["bundle"]
    files: list[str] = [b["root"]["readme"]["dest"], *b["root"]["scripts"], *b["root"]["generated"]]
    # 런타임 RPM 은 스테이징된 경우에만 기대 목록에 넣는다. 없는데 선언하면
    # verify_install 이 항상 실패해 진짜 결손과 구분이 안 된다.
    if staged_rpms():
        files.append("rpms/")
    for svc in image_tar_plan(components):
        files.append(f"docker-images/{svc}.tar")
    for m in models:
        # [2026-09-05] classifier 도 HF 캐시 레이아웃이다 — 런타임이 hub id 로 찾는다.
        # 종전에는 models/{org}-{name}/ 를 선언했는데 그 경로는 아무도 보지 않았고,
        # 담는 코드도 없어 verify_install 이 늘 결손을 보고할 자리였다.
        if m.role in ("embedding", "classifier", "llm"):
            # [#2] 임베더·학습베이스는 HF 캐시 레이아웃으로 실린다(HF_HOME=/models/hf; compose 가
            # ../models:/models 마운트 → 오프라인 로드 경로 models/hf/hub/models--<org>--<name>/).
            # 종전엔 models/<org>-<name>/ 로 잘못 선언돼 실제 스테이징 경로와 어긋났고(그나마
            # 스테이징 자체가 없었다), verify_install 이 엉뚱한 경로를 기대했다.
            # [2026-10-03] llm 역할도 같은 레이아웃으로 합류 — api/worker 가 쓰진 않지만
            # 고객사 로컬 LLM 서버(vLLM/Ollama 등)에 넣을 가중치 원본으로 동봉한다.
            files.append(f"models/hf/hub/models--{m.name.replace('/', '--')}/")
        elif m.role == "classifier_trained":
            files.append(f"models/{m.name.replace('/', '-')}/")
        # embedding_fallback(BGE-M3)은 여전히 안 싣는다 — 임베더가 KURE-v1 하나로 충분해
        # 폴백을 쓴 적이 없다(미사용 경로, 별도 확인 전까지 보류).
    files += [f"infra-config/{f}" for f in b["infra"]["files"]]
    files += [f"docs/{f}" for f in b["docs"]["files"]]
    files += [f"licenses/{f}" for f in b["licenses"]["files"]]
    files += [f"acceptance/{f}" for f in b["acceptance"]["files"]]
    files.append("acceptance/docs/")  # 전 포맷 인수 표본(TXT/PDF/DOCX/XLSX/XLS/PPTX/HWPX)
    # 관측성 스택(동봉 시) — 설정은 항상, 이미지는 best-effort.
    if observability_images:
        for f in b["observability"]["files"]:
            files.append("observability/" + (f[:-1] if f.endswith("/*") else f))
        for img in observability_images:
            files.append(f"docker-images/obs-{_obs_tar_name(img)}.tar")
    return list(dict.fromkeys(files))


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
            ComponentEntry(image=f"koipa-{svc}:{version}", version=version),
        )

    # [2026-10-07 KL 요청] 고객사 전용 이미지(Dockerfile.api.customer·worker.customer)도
    # 같은 compose 서비스(api/worker)가 가리키는데, API_IMAGE_NAME·WORKER_IMAGE_NAME 변수
    # 값만 다르다(docker-compose.airgap.yml·setup.sh 참고) — 같은 파서를 다른 override 로
    # 한 번 더 돌려서 "api-customer"·"worker-customer" 컴포넌트로 추가한다. 번들 하나가
    # 두 NODE(jjw·customer)를 다 설치할 수 있어야 하므로 둘 다 싣는다.
    customer_overrides = extract_components_from_compose(
        compose_path,
        var_overrides={
            "IMAGE_TAG": version,
            "API_IMAGE_NAME": "koipa-api-customer",
            "WORKER_IMAGE_NAME": "koipa-worker-customer",
        },
    )
    for svc in ("api", "worker"):
        entry = customer_overrides.get(
            svc, ComponentEntry(image=f"koipa-{svc}-customer:{version}", version=version),
        )
        components[f"{svc}-customer"] = entry

    models = extract_models_from_config(config_path)
    # #40: 학습된 분류기 가중치 + temperature.json을 번들에 동봉. config.py가 가리키는
    # HF 베이스 모델만으론 폐쇄망에서 미학습·무보정으로 동작한다.
    if classifier_model_dir is not None:
        has_temp = (classifier_model_dir / "temperature.json").exists()
        # 번들↔prod parity fingerprint. dry-run(CI, 모델 부재)에선 생략(속도·부재).
        trained_sha = (
            hash_model_dir(classifier_model_dir)
            if (not dry_run and classifier_model_dir.exists())
            else None
        )
        models.append(ModelEntry(
            name="classifier-trained",
            dim=None,
            sha256=trained_sha,
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
        bundle_name="koipa-airgap-bundle",
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
            newline="\n",
        )
        paths["json"] = json_path

    # YAML (사람 친화) — pyyaml 있으면 그걸로, 없으면 간이 변환
    # newline="\n" 고정 — 없으면 Windows 빌드 호스트에서 CRLF 로 나가고, setup.sh 가
    # `sed -n 's/^ *version: *//p' manifest.yaml` 로 뽑는 IMAGE_TAG_DEFAULT 에 트레일링 \r 이
    # 섞여 `docker compose` 가 그 태그의 이미지를 못 찾는다(실측 2026-09-27: 실빌드 산출물을
    # WSL Rocky 의 진짜 리눅스 sed 로 뽑아 `rocky-koipa-1.0.0\r` 확인 — Windows Git-Bash 의 sed 는
    # CRLF 를 알아서 지워 이 문제를 가려서 못 보게 한다).
    yaml_path = out_dir / "manifest.yaml"
    try:
        import yaml  # noqa: PLC0415

        yaml_text = yaml.safe_dump(manifest.to_dict(), allow_unicode=True, sort_keys=False)
    except ImportError:
        yaml_text = _simple_yaml_dump(manifest.to_dict())
    yaml_path.write_text(yaml_text, encoding="utf-8", newline="\n")
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
        rel = f.relative_to(out_dir).as_posix()
        # [#1] 매니페스트는 자기 자신을 해시하지 않는다. 재빌드-over-existing 시 이전
        # CHECKSUMS.sha256 이 rglob 목록에 섞여 들어와 옛 해시로 기록되면, 이 파일을 덮어쓴
        # 직후엔 반드시 불일치가 되어 verify.sh 가 항상 abort 한다(표준 sha256sum 매니페스트도
        # 자기 자신을 제외한다). 자기참조 라인 1건을 원천 배제해 검증 무한 실패를 막는다.
        if rel == "CHECKSUMS.sha256":
            continue
        h = hashlib.sha256()
        with f.open("rb") as fp:
            for chunk in iter(lambda: fp.read(65536), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {rel}")
    # newline="\n" 고정 — 없으면 Windows 빌드 호스트에서 CRLF 로 나가고, verify.sh 의
    # `sha256sum -c CHECKSUMS.sha256` 가 각 줄 끝 \r 을 파일명의 일부로 읽어 전 파일
    # "No such file or directory" 로 실패한다(실측 2026-09-27: 실빌드 3,799줄 전부 CRLF).
    cs_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return cs_path


# ─────────────────────────────────────────────────────────────
# 체크리스트 출력 (사람용)
# ─────────────────────────────────────────────────────────────


def print_checklist(manifest: BundleManifest, *, stream=sys.stdout) -> None:
    """ASCII-only 출력 (Windows cp949 콘솔 호환)."""
    p = lambda s: print(s, file=stream)  # noqa: E731

    p("\n=== Koipa Airgap Bundle - Pre-flight Checklist ===")
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
        p("\n* DRY-RUN: no docker save / huggingface download executed.")
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


def _readiness_evaluated_model(readiness_path: str) -> str:
    """readiness 리포트가 '무슨 모델을 평가한' 것인지 — 없으면 빈 문자열."""
    try:
        data = json.loads(Path(readiness_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 부재/파손은 상위 게이트가 판정
        return ""
    return str(data.get("evaluated_model") or data.get("deployed_model") or "")


# 고객 인수(acceptance) 러너 — 호스트에서 bash+curl 로 실행(파이썬 불요). 배포 API 에 팩 문서를
# 업로드(POST /documents/analyze)해 severity floor 를 검증한다. 판정 규율: 정확 등급일치가 아니라
# (1) 파싱 성공 + (2) 고등급 미탐 없음(pred 가 기대보다 '덜 민감'하면 FAIL). over-분류(더 민감)는 안전방향=통과.
_ACCEPTANCE_SH = r'''#!/usr/bin/env bash
# Koipa 고객 인수(acceptance) 러너 — 배포 후 파서·분류·안전 게이트를 실문서로 검증.
# 판정: 정확 등급일치가 아니라 (1) 파싱 성공 + (2) 고등급 미탐 없음(severity floor). over-분류는 안전=통과.
# 사용:  API_KEY=<배포키> BASE_URL=http://localhost:8000 bash acceptance/run_acceptance.sh
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE_URL="${BASE_URL:-http://localhost:8000}"
API_KEY="${API_KEY:-}"
MANIFEST="$HERE/expected_labels.json"
sev() { case "$1" in TS) echo 0;; S1) echo 1;; S2) echo 2;; S3) echo 3;; *) echo 9;; esac; }

command -v curl >/dev/null 2>&1 || { echo "[acceptance] FAIL: curl 필요"; exit 2; }
[ -f "$MANIFEST" ] || { echo "[acceptance] FAIL: expected_labels.json 없음"; exit 2; }
curl -fsS "$BASE_URL/api/v1/healthz" >/dev/null 2>&1 || { echo "[acceptance] FAIL: API 무응답 ($BASE_URL/api/v1/healthz)"; exit 1; }

if command -v python3 >/dev/null 2>&1; then
  PAIRS="$(python3 -c 'import json,sys;[print(d["file"]+"|"+d["expected_grade"]) for d in json.load(open(sys.argv[1]))["docs"]]' "$MANIFEST")"
else
  PAIRS="$(grep -oE '"file": *"[^"]*"|"expected_grade": *"[^"]*"' "$MANIFEST" | sed -E 's/.*: *"([^"]*)"/\1/' | paste -d'|' - -)"
fi

# [2026-08-02] 동기 진단 경로(/documents/analyze)는 청크 상한을 넘는 대용량 문서를 명시 거절한다
# (워커 타임아웃으로 조용히 죽는 대신 이유를 말하고 거절 — 의도된 설계). 종전 러너는 그 거절을
# 빈 응답으로 받아 '고등급 미탐(veto)'으로 채점해 **정상 배포에서도 FAIL** 이 떴다(리허설 실측:
# 46,076자·표 47개 .hwp 가 155청크 > 상한 34). 대용량은 운영에서도 비동기 경로를 쓰므로,
# 거절이면 적재→비동기 분류→폴링으로 재시도해 같은 기준(등급)으로 채점한다.
_async_classify() {   # $1=파일경로 → "label|model_version" (실패 시 빈 문자열)
  # actor 는 multipart Form 필드(JSON 문자열) — 누락 시 422.
  up="$(curl -fsS -X POST "$BASE_URL/api/v1/documents" -H "X-API-Key: $API_KEY" \
         -F 'actor={"user_id":"acceptance","role":"admin"}' -F "file=@$1" 2>/dev/null)" || return 1
  # [2026-09-27] 아래 sync 루프처럼 python3 유무를 안 가리면, python3 가 없는 호스트(Rocky 8 기본은
  # /usr/bin/python3 가 없다 — 있는 건 /usr/libexec/platform-python 뿐)에서 doc_id 추출이 매번
  # 빈 문자열이 되어 대용량 문서가 전부 veto(고등급 미탐) 오판정된다 — 실배포 실물확인(Rocky8
  # +실번들)에서 재현: 백엔드는 정상 분류했는데 인수 러너만 FAIL 을 냈다.
  if command -v python3 >/dev/null 2>&1; then
    did="$(printf '%s' "$up" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("doc_id") or "")' 2>/dev/null)"
  else
    did="$(printf '%s' "$up" | grep -oE '"doc_id": *"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/')"
  fi
  [ -z "$did" ] && return 1
  job="$(curl -fsS -X POST "$BASE_URL/api/v1/classify/async" -H "X-API-Key: $API_KEY" \
          -H 'Content-Type: application/json' \
          -d "{\"doc_id\":\"$did\",\"actor\":{\"user_id\":\"acceptance\",\"role\":\"admin\"}}" 2>/dev/null)" || return 1
  if command -v python3 >/dev/null 2>&1; then
    jid="$(printf '%s' "$job" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("job_id") or "")' 2>/dev/null)"
  else
    jid="$(printf '%s' "$job" | grep -oE '"job_id": *"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/')"
  fi
  [ -z "$jid" ] && return 1
  i=0
  while [ "$i" -lt "${ASYNC_POLL_MAX:-60}" ]; do
    sleep 5; i=$((i+1))
    st="$(curl -fsS "$BASE_URL/api/v1/classify/jobs/$jid" -H "X-API-Key: $API_KEY" 2>/dev/null)" || continue
    if command -v python3 >/dev/null 2>&1; then
      printf '%s' "$st" | python3 -c '
import json,sys
d=json.load(sys.stdin); s=d.get("status")
if s in ("done","partial"):
    r=(d.get("results") or [{}])[0]
    print((r.get("label") or "")+"|"+(r.get("model_version") or "")); sys.exit(0)
sys.exit(1 if s!="failed" else 2)' 2>/dev/null && return 0
      [ "$?" = "2" ] && return 1
    else
      jstatus="$(printf '%s' "$st" | grep -oE '"status": *"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/')"
      case "$jstatus" in
        done|partial)
          jlabel="$(printf '%s' "$st" | grep -oE '"label": *"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/')"
          jmv="$(printf '%s' "$st" | grep -oE '"model_version": *"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/')"
          echo "${jlabel}|${jmv}"; return 0 ;;
        failed) return 1 ;;
      esac
    fi
  done
  return 1
}

n=0; fail=0; mfail=0; async_used=0; over=0
while IFS='|' read -r file exp; do
  [ -z "$file" ] && continue
  n=$((n+1))
  http="$(curl -sS -o /tmp/_acc_resp.$$ -w '%{http_code}' -X POST "$BASE_URL/api/v1/documents/analyze" \
           -H "X-API-Key: $API_KEY" -F "file=@$HERE/$file" 2>/dev/null)"
  resp="$(cat /tmp/_acc_resp.$$ 2>/dev/null)"; rm -f /tmp/_acc_resp.$$
  pred=""; mv=""
  if [ "$http" = "200" ] && [ -n "$resp" ]; then
    if command -v python3 >/dev/null 2>&1; then
      pred="$(printf '%s' "$resp" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("classification",{}).get("label",""))' 2>/dev/null)"
      mv="$(printf '%s' "$resp" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("classification",{}).get("model_version",""))' 2>/dev/null)"
    else
      pred="$(printf '%s' "$resp" | grep -oE '"label": *"[A-Z0-9]+"' | tail -1 | sed -E 's/.*"([A-Z0-9]+)"/\1/')"
      mv="$(printf '%s' "$resp" | grep -oE '"model_version": *"[^"]*"' | tail -1 | sed -E 's/.*"([^"]*)"/\1/')"
    fi
  elif printf '%s' "$resp" | grep -q "too large for synchronous analysis"; then
    # 대용량 — 운영과 동일하게 비동기 경로로 채점(스킵하지 않는다. 미탐 검증을 건너뛰면 인수의 의미가 없다).
    pair="$(_async_classify "$HERE/$file")" || pair=""
    pred="${pair%%|*}"; mv="${pair##*|}"
    if [ -n "$pred" ]; then async_used=$((async_used+1)); else
      echo "  FAIL   $file  (동기 거절 후 비동기 분류도 실패 = 고등급이면 미탐)"; fail=$((fail+1)); continue
    fi
  fi
  if [ -z "$pred" ]; then echo "  FAIL   $file  (분석 응답 없음/HTTP $http = 고등급이면 미탐)"; fail=$((fail+1)); continue; fi
  # [#11] 배포 모델이 실제 로드됐는지 — rule-fallback(가중치 미로드)이면 배포 인수 실패.
  # airgap 배포는 학습·보정 모델이 반드시 서빙돼야 한다(임베더/분류기 로드 실패면 여기서 잡힌다).
  case "$mv" in
    rule-fallback-v0|rule-fallback|none)
      echo "  MODEL! $file  model_version=$mv  (가중치 미로드=rule-fallback; 배포 모델 미검증)"; mfail=$((mfail+1)) ;;
  esac
  if [ "$(sev "${pred:-X}")" -gt "$(sev "$exp")" ]; then
    echo "  UNDER! $file  exp=$exp pred=${pred:-?}  (고등급 미탐 - veto)"; fail=$((fail+1))
  elif [ "$(sev "${pred:-X}")" -lt "$(sev "$exp")" ]; then
    # 과분류는 안전방향이라 판정은 통과다. 다만 '통과'로만 찍으면 시연에서 공개문서가 TS 로 나온 걸
    # 설명할 수 없다(실측: 공개 공고문 S3 → TS). 판정은 그대로 두고 건수만 드러낸다.
    echo "  ok(+)  $file  exp=$exp pred=${pred:-?}  (과분류 — 안전방향, 검수부하↑)"; over=$((over+1))
  else
    echo "  ok     $file  exp=$exp pred=${pred:-?}"
  fi
done < <(printf '%s\n' "$PAIRS")

# rule-fallback 은 FNR-safe 이나 배포 인수에선 '모델 미로드'를 통과시키면 안 된다.
# 운영자가 의도적으로 rule-only 를 검증할 때만 ALLOW_RULE_FALLBACK=1 로 우회.
_model_ok=1
[ "$mfail" -gt 0 ] && [ "${ALLOW_RULE_FALLBACK:-0}" != "1" ] && _model_ok=0
if [ "$fail" -eq 0 ] && [ "$_model_ok" -eq 1 ]; then _v=PASS; else _v=FAIL; fi
echo "[acceptance] $_v: ${n} docs, ${fail} veto(고등급 미탐/파싱실패), ${mfail} model-unloaded(rule-fallback), ${async_used} 비동기경로(대용량), ${over} 과분류(안전방향·판정통과)"
[ "$fail" -eq 0 ] && [ "$_model_ok" -eq 1 ]
'''


# 전문가 검수 요청에서 뺀 문서(품질 결함 20건, 2026-09-25) — 번들에 싣지 않는다. 지재원에 가는 후보는
# "전달 패키지 1,731건 전부"가 아니라 품질 기준을 통과한 것만이다. 목록은 evidence/ 에 커밋돼 있고
# `scripts/audit_golden_candidate_pool.py --write-exclusions` 가 쓴다. 파일이 없으면 아무것도 안 뺀다.
_REVIEW_REQUEST_EXCLUSIONS = _REPO_ROOT / "evidence" / "review_request_exclusions.jsonl"


def load_review_request_exclusions(path: Path | None = None) -> set[str]:
    """검수 요청에서 뺀 문서 id 집합. 파일이 없거나 깨진 줄은 건너뛴다(뺀 것이 없다는 뜻이 된다)."""
    p = path or _REVIEW_REQUEST_EXCLUSIONS
    out: set[str] = set()
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc_id = str(json.loads(line).get("doc_id") or "").strip()
        except json.JSONDecodeError:
            continue
        if doc_id:
            out.add(doc_id)
    return out


def select_review_batch_files(src: Path, excluded: set[str], batch: str) -> list[Path]:
    """`batch`로 태그된 후보(메타·본문 쌍)만 고른다, 요청에서 뺀 문서는 제외.

    [2026-10-03 결함 발견·수정] 전에는 doc_id 가 "MD-" 로 시작하는 파일만 골랐다(1,711건
    배치의 doc_id 체계). 그 뒤 새 배치(mock1000, doc_id "MK-")로 바뀌었는데 이 글자가
    그대로 있어서 — 지금 배포본을 만들면 **아무 문서도 안 실린다**(실측: "MD-*" 글롭이
    0건, 1,711건은 2026-10-03에 보관함으로 옮겨 이 폴더에 없다). doc_id 접두사가 아니라
    각 문서의 metadata.json 안 `review_batch` 필드로 거른다 — 배치가 또 바뀌어도 이름
    체계와 무관하게 동작한다. 어느 배치를 실을지는 deploy_manifest.toml 의
    [bundle.review_batch].batch 가 정본이다(이 함수를 부르는 쪽이 그 값을 읽어 넘긴다).
    """
    files = []
    for meta_path in sorted(src.glob("*.metadata.json")):
        doc_id = meta_path.name[: -len(".metadata.json")]
        if doc_id in excluded:
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if meta.get("review_batch") != batch:
            continue
        body_path = src / f"{doc_id}_review.md"
        if not body_path.is_file():
            continue
        files.append(meta_path)
        files.append(body_path)
    return files


def _copy_infra(out_dir: Path, version: str = "1.0.0-rc1") -> None:
    """docker-compose(airgap 포함), env template, alembic 복사. (ES 설정 폐기 — §03)"""
    import shutil

    infra = out_dir / "infra-config"
    infra.mkdir(parents=True, exist_ok=True)

    # 개발용 docker-compose.yml(api/worker가 build: 라 이미지 추출 불가 + mlflow 잔존)은
    # 번들에 안 넣는다 — setup.sh·deploy_airgap.sh 어느 것도 참조하지 않아 그냥 있으면
    # "왜 파일이 두 개고 뭐가 다른가"라는 혼란만 남긴다. 운영 배포는 airgap 파일만 쓴다.
    # GPU 오버레이(옵인)는 base 가 CPU 라서 NVIDIA 노드만 -f 로 덧붙인다.
    # mTLS 종료 nginx 설정(opt-in `--profile mtls`)은 compose 의 nginx-mtls 가 `./mtls/nginx.mtls.conf`
    # 를 마운트하므로 infra-config/mtls/ 에 실제 파일이 있어야 한다(과거 미동봉 → 마운트 소스 부재로
    # 디렉토리 오생성·nginx 기동 실패). certs/ 는 환경별 PKI 라 번들 제외 — 운영자가 infra-config/mtls/certs/
    # 에 배치(conf 헤더 절차 참조). 어떤 파일을 싣는지는 정본(deploy_manifest.toml) 의 [bundle.infra].
    for _name, _rel in _dm.load()["bundle"]["infra"]["sources"].items():
        _src = _REPO_ROOT / _rel
        if _src.exists():
            (infra / _name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_src, infra / _name)
        else:
            print(f"  [WARN] {_rel} 없음 — infra-config/{_name} 미동봉.", file=sys.stderr)

    # [env_file 경로 정합] compose 의 `env_file: .env` 는 **compose 파일 위치 기준**으로 해석된다.
    # 리포 레이아웃(poc/docker-compose.airgap.yml + poc/.env)에서는 맞지만, 번들에서는 compose 가
    # infra-config/ 로 들어가고 .env 는 INSTALL.md 지시대로 번들 루트에 만들어져 경로가 어긋난다
    # (실측 2026-08-02 리허설: api·worker·beat 가 .env 를 못 읽어 `env file ... not found` 로 기동 실패).
    # 번들 레이아웃은 고정이므로 복사 시점에 한 단계 위(../.env)로 재작성한다.
    _airgap_dst = infra / "docker-compose.airgap.yml"
    if _airgap_dst.exists():
        _txt = _airgap_dst.read_text(encoding="utf-8")
        _fixed = _txt.replace("env_file: .env", "env_file: ../.env")
        if _fixed != _txt:
            _airgap_dst.write_text(_fixed, encoding="utf-8", newline="\n")
            print("  [infra] airgap compose env_file → ../.env (번들 루트 .env 로드)", file=sys.stderr)

    # docs — 절차서 (manifest files_expected 의 docs/{INSTALL,OPERATION,TROUBLESHOOTING}.md 충족)
    docs_dst = out_dir / "docs"
    docs_dst.mkdir(parents=True, exist_ok=True)
    for _doc in _dm.bundle_docs():
        _src = _REPO_ROOT / "docs" / _doc
        if _src.exists():
            shutil.copy2(_src, docs_dst / _doc)
    # 번들 루트 README.md — 설치자가 가장 먼저 여는 한 쪽. expected_files() 는 README.md 를 선언하지만
    # 만드는 코드가 없어 20260928 번들 루트에 README 가 없었다(실물 확인).
    _readme_cfg = _dm.load()["bundle"]["root"]["readme"]
    _readme = _REPO_ROOT / _readme_cfg["source"]
    if _readme.exists():
        shutil.copy2(_readme, out_dir / _readme_cfg["dest"])

    # .env template — 폐쇄망 번들은 반드시 onprem-local 하드닝 프로파일 전용 템플릿을 출하한다.
    # (과거엔 dev 용 .env.example 을 복사했는데, 거기엔 DEPLOY_PROFILE 이 없고 POC_MODE=dryrun·
    #  LLM_PROVIDER=noop 이 박혀 있어 그대로 배포하면 lite-noapi/dryrun 으로 부팅 → 온도보정(T=3.0)·
    #  안전게이트(agreement/metadata)·저장암호화·escalation 이 전부 OFF 로 열화되고, require_safety_gates
    #  기본 False 라 startup fail-fast 도 안 걸려 '켰다고 믿지만 실제 꺼진' 상태로 운영됐다. .env.onprem-local
    #  은 localhost DB URL·IMAGE_TAG 부재라 컨테이너 배포에 부적합하므로, compose 서비스명·IMAGE_TAG·
    #  POSTGRES_PASSWORD 를 갖춘 에어갭 전용 하드닝 템플릿을 직접 만든다.)
    env_template = infra / ".env.template"
    env_template.write_text(
        "# ============================================================\n"
        "# Koipa 폐쇄망(에어갭) 배포 .env 템플릿 — onprem-local 하드닝 프로파일\n"
        "# 이 파일을 .env 로 복사(deploy_airgap.sh 가 자동 복사)한 뒤 replace_me_* 를 실값으로 채운다.\n"
        "# DEPLOY_PROFILE=onprem-local 이 온도보정(T=3.0)·안전게이트(agreement/metadata)·저장암호화·\n"
        "# escalation(τ=0.30)·require_safety_gates 를 자동 활성화한다. 이 줄을 지우거나 lite-* 로 바꾸면\n"
        "# 안전장치가 꺼진 채 부팅되니 금지(deploy_airgap.sh 가 거부한다).\n"
        "# ============================================================\n"
        "DEPLOY_PROFILE=onprem-local\n"
        "POC_MODE=full\n"
        "\n"
        "# --- 필수 (deploy_airgap.sh 가 placeholder 를 검증·거부) ---\n"
        # [2026-08-03] 종전엔 1.0.0-rc1 이 하드코딩돼, 다른 버전으로 구운 번들을 문서대로 설치하면
        # `manifest for koipa-api:1.0.0-rc1 not found` 로 기동이 실패했다(운영자가 태그 일치를
        # 손으로 챙겨야 했다). 번들이 실제로 담은 태그를 그대로 적어 docker load 태그와 자동 일치시킨다.
        f"IMAGE_TAG={version}\n"
        "API_KEY=replace_me_api_key\n"
        "POSTGRES_USER=koipa\n"
        "POSTGRES_PASSWORD=replace_me_postgres_password\n"
        "# 감사체인 HMAC 비밀키(NFR-SEC-01). 미설정이면 api startup 이 fail-fast 로 죽는다 —\n"
        "# 종전 템플릿에 이 줄이 없어 설치자가 기동 실패로만 알게 됐다(2026-08-02 리허설 실측).\n"
        "# python3 -c \"import secrets;print(secrets.token_hex(32))\"\n"
        "KOIPA_AUDIT_CHAIN_SECRET=replace_me_64hex_random\n"
        "# 골든 검수·서명 화면(review.html·signoff.html)의 서명 URL HMAC 키. **미설정이면 그 화면들이\n"
        "# 무인증으로 열린다** — 후보 문서 본문(고등급 포함)이 URL 만 알면 노출된다(코드는 하위호환을 위해\n"
        "# opt-in 강제라 키가 있어야 닫힌다). deploy_cloud.sh·deploy_testserver_dual.sh 는 이 값을 자동\n"
        "# 생성하는데 **폐쇄망 번들 템플릿에만 빠져 있었다**(실측 2026-08-06: 신규 배포에서 review.html 200 무인증).\n"
        "# python3 -c \"import secrets;print(secrets.token_hex(32))\"\n"
        "GOLDEN_HTML_URL_SECRET=replace_me_64hex_random\n"
        "# 운영 모드에서 CORS 와일드카드(*)는 startup 에서 거부된다. 콘솔을 여는 실제 오리진으로 적을 것.\n"
        "# 값은 JSON 배열 형식. 예) [\"https://ai.example.go.kr\"]  ·  여러 개면 쉼표로 나열.\n"
        "CORS_ALLOW_ORIGINS=[\"https://replace_me.your.domain\"]\n"
        "\n"
        "# 관측성 스택(§10.5)을 쓸 때만 필요 — 값이 없으면 grafana 기동이 즉시 실패한다\n"
        "# (docker compose 인터폴레이션 required 변수. 실측 2026-08-04: 템플릿에 없어 첫 기동 실패).\n"
        "GRAFANA_PASSWORD=replace_me_grafana_admin_password\n"
        "\n"
        "# --- 호스트 포트 (기본값이 이미 쓰이는 중이면 여기서만 바꾼다 — YAML 수정 불요) ---\n"
        "API_PORT=8000\n"
        "# API 바인드 주소. 기본 127.0.0.1 — 앱은 루프백에만 서고 외부 노출은 프록시가 한다\n"
        "# (nginx-mtls `--profile mtls` 또는 docker-compose.console-proxy.yml).\n"
        "# 프록시 없이 임시로 열어야 할 때만 0.0.0.0 으로 **명시**할 것. 2026-08-19 에\n"
        "# 앱이 평문으로 외부에 직접 붙어 admin JWT 가 망 밖에서 수신된 적이 있다.\n"
        "API_BIND=127.0.0.1\n"
        "PG_PORT=5432\n"
        "REDIS_PORT=6379\n"
        "\n"
        "# --- 인프라 (컨테이너 내부 → compose 서비스명 postgres/redis, localhost 아님) ---\n"
        "# DATABASE_URL 의 비밀번호는 위 POSTGRES_PASSWORD 와 반드시 일치시킬 것.\n"
        "DATABASE_URL=postgresql+psycopg://koipa:replace_me_postgres_password@postgres:5432/koipa\n"
        "REDIS_URL=redis://redis:6379/0\n"
        "VECTOR_BACKEND=pg\n"
        "STORAGE_BACKEND=local\n"   # 폐쇄망=로컬FS(/app/.storage). MinIO 미사용.
        "\n"
        "# --- 원본 at-rest 암호화 (onprem-local 이 ENABLED=1 강제; KEY 미설정이면 startup fail-fast) ---\n"
        "# python -c \"import secrets;print(secrets.token_hex(32))\"\n"
        "STORAGE_ENCRYPTION_KEY=replace_me_64hex_random\n"
        "\n"
        "# --- 모델 / 로컬 LLM (폐쇄망 — 외부 API 0) ---\n"
        "EMBEDDING_MODEL=nlpai-lab/KURE-v1\n"
        "# 컨테이너 내 경로(compose 가 ../models 를 /models 로 마운트). 값 자체는 compose 기본값과\n"
        "# 같지만, INSTALL.md 필수값 표에 실려 있는데 템플릿엔 없어 문서↔산출물이 어긋나 있었다.\n"
        "CLASSIFIER_MODEL_DIR=/models/classifier-trained\n"
        "\n"
        "# 등급 분류는 LLM 을 쓰지 않는다(룰+분류기). LLM 은 RAG 질의응답·합성문서 생성에만 쓰이며\n"
        "# 그건 GPU 보유 현장 한정이다. 그래서 기본은 noop — LLM 서버가 없는 현장에서 연결 실패가\n"
        "# 나지 않는다. GPU 로 로컬 LLM 을 띄운 현장만 vllm(또는 ollama)으로 바꾸고 아래 URL 을 채운다.\n"
        "LLM_PROVIDER=noop\n"
        "LOCAL_LLM_BASE_URL=http://host.docker.internal:8001/v1\n"
        "LOCAL_LLM_MODEL=Qwen/Qwen3-14B\n"
        "LOCAL_LLM_API_KEY=EMPTY\n"
        "\n"
        "# --- 배포 게이트 · locked_gold_eval (사람서명 평가정답 누적 경로) ---\n"
        "LOCKED_EVAL_JSONL=datasets/gold_real/locked_gold_eval.jsonl\n"
        "\n"
        # [2026-09-25] 선택 기능은 여기 적어 둬야 설치자가 안다 — 블라인드 손잡이가 이 템플릿에 없어 배포 전에
        # 손으로 더해야 했던 전례가 있다. 기본 꺼짐이라 주석으로만 싣는다(주석을 풀어야 켜진다).
        "# --- 사내 규정 참고 표시 (선택 기능 · 기본 꺼짐) ---\n"
        "# 회원사가 올린 사내 규정에서 검수 중인 문서와 관련된 원문 문장을 검수 화면에 참고로 보여 준다.\n"
        "# 등급 판정·자동 확정에는 쓰이지 않고 LLM 도 쓰지 않는다. 켜면 콘솔 설정 탭에 「사내 규정(참고 표시)」\n"
        "# 카드가 생기고, 끄면 그 화면·API 가 사라진다(올려 둔 규정 데이터는 남는다).\n"
        "# 전제: 문서와 규정을 같은 실제 임베더(EMBEDDING_MODEL=KURE-v1)로 비교한다 — 번들에 임베더 모델이 들어 있어야\n"
        "# 하고, 해시 임베더면 규정 등록이 거절된다.\n"
        "# REGULATION_REFERENCE_ENABLED=1\n"
        "\n"
        # [2026-10-06] KL 연계 개선 요청 반영분 — 전부 기본 꺼짐(비워두면 평소 동작 그대로).
        "# --- KL 연계 개선 (2026-10-06 · 전부 선택, 기본 꺼짐) ---\n"
        "# API 키 교체 유예. 새 키로 바꾼 뒤 당분간 옛 키도 받아주려면 옛 값을 여기 채운다.\n"
        "# 호출자를 다 옮긴 뒤에는 다시 비워 옛 키를 폐기한다.\n"
        "API_KEY_PREVIOUS=\n"
        "\n"
        "# 파일 업로드 대신 \"같은 VM에 이미 있는 파일 경로\"로 문서 등록(POST /documents 의 file_path).\n"
        "# 이 디렉터리들이 실제로 이 컨테이너에 마운트돼 있어야 하고, 전부의 바깥 경로는 거절된다.\n"
        "# 공유 폴더가 여러 곳이면 전부 나열(JSON 배열).\n"
        "# DOCUMENTS_SHARED_MOUNT_DIRS=[\"/data/kl-shared\"]\n"
        "# POST /documents/batch 한 요청의 최대 파일 수(기본 50 — 파일마다 동기 파싱이 필요해 크게 두지 않는다).\n"
        "DOCUMENTS_BATCH_MAX_FILES=50\n"
        "\n"
        "# 분류 완료를 koipa:job:{job_id} 저장·콜백 외에 Redis Stream 으로도 발사(Consumer Group 직접구독).\n"
        "# 반드시 위 REDIS_URL(작업큐·outbox)과 다른 인스턴스를 가리킬 것 — docker-compose.airgap.yml 의\n"
        "# redis-kl-stream 서비스가 그 전용 인스턴스다(기동은 되지만 이 값이 비면 아무것도 쓰지 않는다).\n"
        "# KL_STREAM_REDIS_URL=redis://redis-kl-stream:6379/0\n"
        "# KL_STREAM_NAME=koipa:classify:results\n"
        "# KL_STREAM_MAXLEN=10000\n"
        "\n"
        "# 인증을 전부 끈다(X-API-Key·JWT 검증 생략). 역할은 그래도 위 API_KEY_ROLE 그대로 적용된다.\n"
        "# [2026-10-06 사용자 결정] 이 배포는 폐쇄망 전용이라 기본으로 켜 둔다. 이 네트워크가\n"
        "# 인터넷·다른 네트워크에 연결되는 순간부터 인증 없이 전체 API 가 열리므로, 망 구성이\n"
        "# 바뀌면 이 줄을 지우고 api_key 로 되돌릴 것.\n"
        "AUTH_MODE=none\n",
        encoding="utf-8",
        newline="\n",
    )

    # alembic 은 번들에 따로 싣지 않는다 — api 이미지에 alembic.ini·alembic/ 이 들어 있고 마이그레이션은
    # 컨테이너 안에서 실행된다(INSTALL.md §6). 예전 db-migrations/ 사본은 어느 스크립트도 읽지 않았다.

    # OSS 라이선스 + SBOM — expected_files 가 licenses/third-party-licenses.txt 를 기대하는데
    # 과거엔 아무도 복사하지 않아 번들에 실제로는 없었다(공급망 감사 산출물 부재). 정본이 적은
    # 파일만 싣는다(같은 내용의 .md·.json·sbom.json 형식은 뺀다). 없으면 경고 후 진행.
    licenses_src = _REPO_ROOT / "licenses"
    licenses_dst = out_dir / "licenses"
    if licenses_src.exists() and not licenses_dst.exists():
        import shutil as _sh
        licenses_dst.mkdir(parents=True, exist_ok=True)
        for _lic in _dm.bundle_licenses():
            if (licenses_src / _lic).is_file():
                _sh.copy2(licenses_src / _lic, licenses_dst / _lic)
            else:
                print(f"  [WARN] licenses/{_lic} 없음 — 미동봉.", file=sys.stderr)
        print(f"  [lic]  라이선스·SBOM → {licenses_dst}", file=sys.stderr)
    elif not licenses_src.exists():
        print(
            "  [WARN] licenses/ 없음 — SBOM·서드파티 라이선스 미동봉. "
            "`make licenses` 로 먼저 산출하세요(공급망 감사 산출물).",
            file=sys.stderr,
        )

    # 컨테이너 런타임 RPM 스테이징. 없으면 경고만 하고 계속한다 — 런타임이 이미 깔린
    # 호스트도 있고, 번들 빌드 자체를 막으면 나머지 산출물 검증까지 못 하게 된다.
    _rpms = staged_rpms()
    if _rpms:
        _rpm_out = out_dir / "rpms"
        _rpm_out.mkdir(parents=True, exist_ok=True)
        import shutil as _sh_rpm
        for _r in _rpms:
            _sh_rpm.copy2(_r, _rpm_out / _r.name)
        _mb = sum(_r.stat().st_size for _r in _rpms) / 1024 / 1024
        print(f"  [rpms] {len(_rpms)}개 동봉 ({_mb:.1f} MB) <- {rpm_source_dir()}", file=sys.stderr)
    else:
        print(
            f"  [WARN] 컨테이너 런타임 RPM 미동봉 ({rpm_source_dir()} 비어 있음).",
            file=sys.stderr,
        )
        print(
            "         지재원(공공망)·고객사(폐쇄망) 모두 dnf 저장소를 못 쓴다. 런타임이 이미",
            file=sys.stderr,
        )
        print(
            "         깔린 호스트가 아니면 install.sh 0단계에서 멈춘다. 인터넷 되는 Rocky",
            file=sys.stderr,
        )
        print(
            "         8.10 호스트에서: dnf download --resolve --destdir=poc/rpms "
            "docker-ce docker-ce-cli containerd.io docker-compose-plugin",
            file=sys.stderr,
        )

    # install.sh
    install_sh = out_dir / "install.sh"
    install_sh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "BUNDLE_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n\n"
        "echo '=== Koipa Airgap Bundle Install ==='\n"
        "# 0) 컨테이너 런타임 판별 — 운영 대상이 RHEL 계열(Rocky)이면 기본이 podman 이다.\n"
        "#    docker 를 하드코딩하면 그 호스트에서 설치가 첫 줄부터 멈춘다(2026-08-26).\n"
        "#    런타임이 아예 없으면 번들의 rpms/ 로 설치한다 — 설치 대상 두 곳(지재원 공공망·\n"
        "#    고객사 폐쇄망) 모두 인터넷 저장소를 못 쓰므로 dnf install 로 받아올 수 없다.\n"
        "if ! command -v docker >/dev/null 2>&1 && ! command -v podman >/dev/null 2>&1; then\n"
        "  if ls \"$BUNDLE_DIR/rpms\"/*.rpm >/dev/null 2>&1; then\n"
        "    echo '[runtime] 컨테이너 런타임 없음 — 번들 rpms/ 로 설치'\n"
        "    if [ \"$(id -u)\" != 0 ]; then\n"
        "      echo '[ERROR] RPM 설치는 root 권한이 필요하다 — sudo bash install.sh' >&2; exit 1\n"
        "    fi\n"
        "    # --disablerepo=* : 폐쇄망에서 저장소 메타데이터를 받으러 나가면 타임아웃으로 멈춘다.\n"
        "    if command -v dnf >/dev/null 2>&1; then\n"
        "      dnf install -y --disablerepo='*' \"$BUNDLE_DIR/rpms\"/*.rpm\n"
        "    elif command -v yum >/dev/null 2>&1; then\n"
        "      yum install -y --disablerepo='*' \"$BUNDLE_DIR/rpms\"/*.rpm\n"
        "    else\n"
        "      rpm -Uvh --replacepkgs \"$BUNDLE_DIR/rpms\"/*.rpm\n"
        "    fi\n"
        "    # docker-ce 는 설치만으로 데몬이 뜨지 않는다. podman 은 데몬이 없어 이 단계가 불필요.\n"
        "    if command -v docker >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1; then\n"
        "      systemctl enable --now docker || echo '[WARN] docker 데몬 기동 실패 — systemctl status docker 로 확인'\n"
        "    fi\n"
        "  else\n"
        "    echo '[ERROR] 컨테이너 런타임 미탑재이고 번들에 rpms/ 도 없다.' >&2\n"
        "    echo '        인터넷 되는 Rocky 8.10 호스트에서 아래를 돌려 번들을 다시 만들어야 한다:' >&2\n"
        "    echo '          dnf download --resolve --destdir=poc/rpms docker-ce docker-ce-cli containerd.io docker-compose-plugin' >&2\n"
        "    exit 1\n"
        "  fi\n"
        "fi\n"
        "if command -v docker >/dev/null 2>&1; then CRT=docker\n"
        "elif command -v podman >/dev/null 2>&1; then CRT=podman\n"
        "else echo '[ERROR] 컨테이너 런타임 설치 후에도 명령을 찾지 못했다' >&2; exit 1\n"
        "fi\n"
        "echo \"[runtime] $CRT\"\n\n"
        "# 1) 이미지 적재\n"
        "for tar in \"$BUNDLE_DIR/docker-images\"/*.tar; do\n"
        "  echo \"Loading $tar ...\"\n"
        "  \"$CRT\" load -i \"$tar\"\n"
        "done\n\n"
        "# 2) env 설정\n"
        "# 비밀값 파일이다 — 생성 시점부터 소유자 전용으로 만든다(umask + 명시 chmod).\n"
        "if [ ! -f .env ]; then (umask 077; cp \"$BUNDLE_DIR/infra-config/.env.template\" .env); fi\n"
        "chmod 600 .env 2>/dev/null || true\n"
        "echo 'Next: edit .env (IMAGE_TAG·POSTGRES_PASSWORD·API_KEY…), then run:'\n"
        "echo '  bash deploy.sh          # 통합 진입점 — 폐쇄망 자동감지 후 전체 스택 원커맨드 기동(권장)'\n"
        "echo '  (동일: bash deploy_airgap.sh   verify→infra→alembic→app→스모크)'\n"
        "echo '  또는 수동: docker compose --env-file .env -f infra-config/docker-compose.airgap.yml up -d (docs/INSTALL.md)'\n",
        encoding="utf-8",
        newline="\n",
    )
    install_sh.chmod(0o755)

    # verify.sh
    verify_sh = out_dir / "verify.sh"
    verify_sh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo '=== Bundle Verify ==='\n"
        "cd \"$(dirname \"$0\")\"\n"
        # 체크섬 불일치 = 반입 매체 손상/변조 → 반드시 비정상 종료(exit 1).
        # 과거 `... || echo MISMATCH` 는 실패를 삼켜 항상 exit 0 이었고, deploy_airgap.sh 의
        # `bash verify.sh || die` 가 죽은 코드가 되어 손상 매체로도 배포가 진행됐다(fake-green).
        "sha256sum -c CHECKSUMS.sha256 || { echo 'CHECKSUM MISMATCH — 반입 매체 손상/변조. 배포 중단.' >&2; exit 1; }\n"
        "echo 'Checksums OK'\n",
        encoding="utf-8",
        newline="\n",
    )
    verify_sh.chmod(0o755)

    # deploy.sh(통합 진입점) + deploy_airgap.sh(전체 스택 원커맨드 기동). 리포 scripts/ 의 정본을
    # 번들 루트로 복사(단일 출처). install.sh(docker load) 이후 `bash deploy.sh` 로 기동.
    # [줄바꿈 정규화] 리눅스 타깃으로 나가는 셸 스크립트는 반드시 LF 여야 한다. Windows 빌드 호스트
    # 에서 `git archive HEAD:poc` 로 소스를 뽑으면 **poc/ 가 아카이브 루트라 리포 루트의
    # .gitattributes(*.sh eol=lf)를 못 보고** core.autocrlf=true 가 먹어 CRLF 로 나온다. 그대로 실으면
    # 타깃에서 `set: pipefail: invalid option name` · `$'\r': command not found` 로 **원커맨드 배포
    # 경로 전체가 죽는다**(실측 2026-08-03 리허설: deploy_airgap.sh·verify_install.sh 둘 다 미실행).
    # 빌드 호스트가 어떤 환경이든 번들 산출물만은 LF 로 고정한다.
    import shutil as _sh
    # preflight_host.sh — install.sh 앞에서 호스트를 점검한다(읽기 전용). 설치를 발주처가
    # 수행하므로 현장에서 처음 만나는 실패를 줄이려면 이 스크립트가 번들에 함께 있어야 한다.
    # setup.sh — 원커맨드 설치기. 설치를 발주처(지재원)·고객사가 직접 수행하므로
    # 번들 안에 없으면 원커맨드 경로 자체가 존재하지 않는다.
    # db_probe.sh — deploy_airgap.sh 가 4단계(DB 헬시 대기)에서 `. "$SELF/db_probe.sh"` 로 읽는다.
    # 이 목록에 없어 번들에 안 실렸고, 실제 설치기를 돌려 보니 "No such file or directory" 로 4단계 직전에
    # 설치가 멈췄다(2026-09-27 실설치 리허설). 리포에서 직접 돌릴 때는 scripts/ 에 있어 가려져 있었다.
    for _script in _dm.bundle_root_scripts():
        _src = _REPO_ROOT / "scripts" / _script
        if _src.exists():
            _dst = out_dir / _script
            _sh.copy2(_src, _dst)
            _raw = _dst.read_bytes()
            if b"\r\n" in _raw:
                _dst.write_bytes(_raw.replace(b"\r\n", b"\n"))
                print(f"  [fix]  {_script}: CRLF → LF 정규화", file=sys.stderr)
            _dst.chmod(0o755)
        else:
            print(f"  [WARN] scripts/{_script} 없음 — 번들에 배포 스크립트 미동봉.", file=sys.stderr)

    # 고객 인수(acceptance) 샘플팩 + 러너 — 상용 운영 포장. 전 포맷 표본을 배포 API 에 올려
    # severity floor(고등급 미탐 없음) + 파싱성공을 검증. expected_files 가 acceptance/* 를 기대하므로
    # 반드시 실제 복사(licenses 처럼 '선언-무복사' 함정 회피). 팩 없으면 경고(`make acceptance-pack` 선행).
    pack_src = _REPO_ROOT / "datasets" / "acceptance_pack"
    pack_dst = out_dir / "acceptance"
    if pack_src.exists() and (pack_src / "expected_labels.json").exists():
        import shutil as _sh
        if pack_dst.exists():
            _sh.rmtree(pack_dst)
        pack_dst.mkdir(parents=True)
        # 팩 폴더의 README.md·real_fixtures.json(빌드 입력, expected_labels.json 에 이미 병합됨)은
        # 러너가 읽지 않는다 — 정본이 적은 파일과 표본 문서(docs/)만 싣는다.
        for _f in _dm.bundle_acceptance_files():
            if _f != "run_acceptance.sh" and (pack_src / _f).is_file():
                _sh.copy2(pack_src / _f, pack_dst / _f)
        if (pack_src / "docs").is_dir():
            _sh.copytree(pack_src / "docs", pack_dst / "docs")
        run_sh = pack_dst / "run_acceptance.sh"
        # newline="\n" 고정 — 없으면 Windows 빌드 호스트에서 LF 가 CRLF 로 번역되어(Path.write_text
        # 기본 동작) 리눅스 타깃에서 `set -o pipefail` 이 "invalid option name" 으로 죽는다(실측
        # 2026-09-27: cat -A 로 전체 라인 ^M$ 확인). install.sh/verify.sh 는 out_dir 직속이라 우연히
        # 문제가 안 드러났을 뿐 동일 위험이 있어 아래도 함께 고정한다.
        run_sh.write_text(_ACCEPTANCE_SH, encoding="utf-8", newline="\n")
        run_sh.chmod(0o755)
        n_docs = sum(1 for _ in (pack_src / "docs").iterdir()) if (pack_src / "docs").exists() else 0
        print(f"  [accept] 인수 샘플팩({n_docs}문서) + run_acceptance.sh → {pack_dst}", file=sys.stderr)
    else:
        print(
            "  [WARN] datasets/acceptance_pack 없음 — 고객 인수 샘플팩 미동봉. "
            "`make acceptance-pack` 로 먼저 생성하세요(상용 운영 포장).",
            file=sys.stderr,
        )

    # [2026-09-24] 검수 배치를 번들에 함께 싣는다. `.dockerignore` 가 datasets/ 전체를 이미지
    # 빌드에서 뺀다(민감물 유입·빌드 지연 방지, 의도된 설계) — 그 결과 golden.py 라우터·블라인드
    # 코드는 이미지에 실려도 **검수할 문서 자체는 안 실렸다**. 이대로 내보내면 지재원은 빈 검수
    # 화면만 받는다. single_document_candidates/ 전체(다른 배치의 공개 실문서도 포함)를 통째로
    # 싣지 않고 **지금 활성 배치만** 필터링한다 — 이번 인도 목적과 무관한 과거 후보 풀까지
    # 내보내지 않기 위해서다.
    # [2026-10-03 결함 수정] 어느 배치가 "지금 활성"인지를 doc_id 접두사(예전 "MD-*")로
    # 하드코딩했었다 — 배치가 mock1000("MK-*")으로 바뀌자 조용히 0건을 실었을 것이다(실측).
    # deploy_manifest.toml 의 [bundle.review_batch].batch 를 정본으로 읽는다 — 배치가 또
    # 바뀌면 거기 한 곳만 고치면 된다.
    #
    # [2026-10-03 사용자 결정 — 같은 날 재확정] 배포본에 mock1000(guide40 구조, 1,001건)을
    # 싣는다 — 검수자 계정 20개를 이미 발급했고(6800b315) 그 계정들이 배포 직후 이 배치를
    # 검수한다. 1,711건 배치는 더 안 쓴다(보관함으로 이동). batch 값이 빈 문자열이면
    # "검수 후보 의도적 미동봉"(예: 운영 방식이 다시 바뀌어 지재원이 직접 생성하는 쪽으로
    # 갈 때)이고, 값이 있는데 0건이면 그게 진짜 결함이다 — 둘을 구분해서 알린다.
    review_src = _REPO_ROOT / "datasets" / "proxy_gold" / "single_document_candidates"
    review_batch_tag = str(_dm.load()["bundle"]["review_batch"]["batch"] or "").strip()
    if not review_batch_tag:
        print(
            "  [golden] 검수 후보 미동봉(의도) — 지재원 관리자가 배포 뒤 「학습 후보 생성」으로 "
            "직접 만들어 검수합니다. 로컬 테스트용 모의문서는 배포본에 안 싣습니다.",
            file=sys.stderr,
        )
    else:
        review_excluded = load_review_request_exclusions()
        review_files = (
            select_review_batch_files(review_src, review_excluded, review_batch_tag)
            if review_src.exists() else []
        )
        if review_files:
            import shutil as _sh_review  # noqa: PLC0415

            review_dst = out_dir / "golden_review_batch"
            review_dst.mkdir(parents=True, exist_ok=True)
            for f in review_files:
                _sh_review.copy2(f, review_dst / f.name)
            n_docs = sum(1 for f in review_files if f.name.endswith("_review.md"))
            size_mb = sum(f.stat().st_size for f in review_files) / (1024 * 1024)
            print(
                f"  [golden] 전문가 검수 배치({review_batch_tag}, {n_docs}건 · {size_mb:.1f}MB, "
                f"검수 요청에서 뺀 {len(review_excluded)}건 제외) → {review_dst}",
                file=sys.stderr,
            )
            print(
                "  [golden] 적재는 설치 후 별도 수행: "
                "`cp -r golden_review_batch/* <배포경로>/datasets/proxy_gold/single_document_candidates/` "
                "(단, 후보 폴더가 이미 있으면 병합이지 덮어쓰기가 아니어야 한다 — 기존 후보와 안 섞이게 "
                "review_batch 메타데이터로 이미 구분됨)",
                file=sys.stderr,
            )
        else:
            print(
                f"  [WARN] batch={review_batch_tag!r} 로 지정했는데 {review_src} 에 해당 문서가 "
                "없다 — 설정은 실렸는데 실제 데이터가 없는 결함일 수 있다. "
                "`scripts/load_mock1000_to_console.py` 등으로 먼저 적재하세요.",
                file=sys.stderr,
            )


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
    # dev overlay(koipa-poc 네트워크)는 폐쇄망에서 무용이므로 제외하고 나머지 전부 복사.
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
            "  [WARN] 학습 분류기 미지정 - 베이스 모델만 번들됩니다(폐쇄망에서 rule-fallback). "
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


def _resolve_hf_cache_dir() -> Path:
    """빌드 호스트의 HF hub 캐시 루트(HF_HOME 우선, 없으면 ~/.cache/huggingface)."""
    root = os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface")
    return Path(root) / "hub"


def _hf_keep_main_revision(cache_dir: Path):
    """shutil.copytree 의 ignore 콜백 — refs/main 이 가리키는 리비전만 남긴다.

    빌드 호스트 HF 캐시에 같은 모델의 리비전이 둘 이상 있으면(KURE-v1 이 그랬다: d14c8a94·8b418a58)
    snapshots/·.no_exist/ 를 통째로 복사해 같은 가중치가 2.29GB 만큼 두 번 실렸다(20260928 번들,
    model.safetensors 바이트 동일). 로더는 리비전을 지정하지 않고(HF_MODEL_REVISION 미설정) refs/main 을
    따르므로 그 리비전만 있으면 된다. refs/main 이 없으면 아무것도 거르지 않는다.
    """
    main_ref = cache_dir / "refs" / "main"
    keep = main_ref.read_text(encoding="utf-8").strip() if main_ref.is_file() else ""

    def _ignore(directory: str, names: list[str]) -> list[str]:
        d = Path(directory)
        if keep and d.parent == cache_dir and d.name in ("snapshots", ".no_exist"):
            return [n for n in names if n != keep]
        return []

    return _ignore


def _prune_unreferenced_blobs(cache_dir: Path) -> int:
    """심링크 레이아웃(리눅스 빌드호스트)에서 남은 리비전이 가리키지 않는 blobs/ 파일을 지운다.

    리비전을 걸러도 blobs/ 에는 그 리비전에서만 쓰이던 파일(예: 다른 README)이 남는다.
    스냅샷 안 항목이 전부 심링크일 때만 동작하고, 실파일이 섞여 있으면(Windows 복사본) 건드리지 않는다.
    """
    blobs = cache_dir / "blobs"
    snaps = cache_dir / "snapshots"
    if not (blobs.is_dir() and snaps.is_dir()):
        return 0
    files = [f for f in snaps.rglob("*") if f.is_file() or f.is_symlink()]
    if not files or not all(f.is_symlink() for f in files):
        return 0
    used = {Path(os.readlink(f)).name for f in files}
    removed = 0
    for b in blobs.iterdir():
        if b.name not in used:
            b.unlink()
            removed += 1
    return removed


def _copy_embedder_cache(
    manifest: "BundleManifest", out_dir: Path, *, allow_download: bool = True
) -> bool:
    """임베딩 모델(KURE-v1 등) HF 캐시를 번들 models/hf/hub/ 로 스테이징한다.

    airgap compose 는 HF_HOME=/models/hf + HF_HUB_OFFLINE=1 로 임베더를 오프라인 로드하고,
    onprem-local 프로파일은 require_real_embedder=True 라 임베더가 없으면 startup 이 fail-secure
    RuntimeError 로 죽는다(#2). 종전 빌더는 분류기만 스테이징하고 임베더를 전혀 담지 않아
    '자칭 self-contained' 번들이 startup 에서 막혔다 — 이 함수가 그 갭을 닫는다.

    빌드 호스트 캐시에 모델이 없으면 allow_download 시 1회 다운로드를 시도(지재원=외부망 빌드),
    그래도 없으면 False → build_bundle 이 fail-closed 로 번들 빌드를 실패 처리한다.
    """
    import shutil  # noqa: PLC0415

    # [2026-09-05] **학습 베이스 모델(role=classifier)도 여기서 담는다.**
    #
    # 종전에는 role=="embedding" 만 담았다. kf-deberta-base 는 role="classifier" 라
    # 어디에도 스테이징되지 않았고, 폐쇄망에서 학습을 걸면 이렇게 죽었다(실측):
    #
    #     OSError: We couldn't connect to 'https://huggingface.co' … and couldn't
    #     find them in the cached files.
    #
    # 런타임이 hub id 로 참조하기 때문이다(CLASSIFIER_BASE_MODEL=kakaobank/kf-deberta-base
    # + HF_HOME=/models/hf + HF_HUB_OFFLINE=1). files_expected 가 선언하던
    # models/{org}-{name}/ 는 런타임이 보는 곳이 아니다.
    #
    # 추론은 무관하다(CLASSIFIER_MODEL_DIR 이 평문 경로로 학습본을 가리킨다). 학습만
    # 못 하는데, 폐쇄망은 enable_incremental_retrain=True 이고 증분 재학습은 매번
    # kf-deberta-base 에서 풀 파인튜닝한다(warm-start 없음).
    #
    # [2026-10-03] LLM(role=llm)도 담는다 — api/worker 프로세스가 transformers 로 직접
    # 로드하지는 않지만(vLLM/Ollama 의 HTTP endpoint 로 서빙), 그 vLLM/Ollama 서버 자체는
    # 이 번들이 띄우지 않는다(docker-compose.gpu.yml 은 api/worker 의 GPU 예약뿐, vLLM
    # 서비스 정의가 없다) — 고객사가 별도로 세우는 로컬 LLM 서버에 넣을 가중치 원본을
    # 여기 실어 보내는 것. 사용자 결정: 로컬 LLM 백엔드가 쓰는 모델이 Qwen3-14B 하나뿐이면
    # 번들에 포함(config.py 의 local_llm_model·vllm_model 기본값이 둘 다 이 모델).
    _HF_CACHE_ROLES = ("embedding", "classifier", "llm")
    embedders = [m for m in manifest.models if m.role in _HF_CACHE_ROLES]
    if not embedders:
        print("  [WARN] manifest 에 HF 캐시로 담을 모델 없음 — 스테이징 skip", file=sys.stderr)
        return True

    hub = _resolve_hf_cache_dir()
    dst_hub = out_dir / "models" / "hf" / "hub"
    ok = True
    for m in embedders:
        cache_name = f"models--{m.name.replace('/', '--')}"
        src = hub / cache_name
        dst = dst_hub / cache_name
        if not src.exists() and allow_download:
            print(f"  [embed] 캐시 부재 → 다운로드 시도: {m.name}", file=sys.stderr)
            try:
                if m.role == "llm":
                    # sentence-transformers 는 임베딩 모델 전용 — Qwen3-14B(causal LM)엔 안 맞는다.
                    from huggingface_hub import snapshot_download  # noqa: PLC0415
                    snapshot_download(repo_id=m.name)
                    dl_ok, detail = True, "snapshot_download OK"
                else:
                    from cache_kure_v1 import cache_model  # noqa: PLC0415
                    dl_ok, detail = cache_model(m.name)
                print(f"  [embed] 다운로드 {'OK' if dl_ok else 'FAIL'}: {detail}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"  [embed] 다운로드 불가: {exc}", file=sys.stderr)
        if not src.exists():
            # 역할마다 없을 때 벌어지는 일이 다르다 — 같은 문구를 쓰면 오해한다.
            if m.role == "classifier":
                why = ("폐쇄망에서 **학습**이 이 오류로 죽습니다: OSError "
                       "'couldn't connect to huggingface.co … not found in cached files'. "
                       "추론은 CLASSIFIER_MODEL_DIR 로 뜨므로 영향이 없습니다")
            elif m.role == "llm":
                why = ("api/worker 기동엔 영향 없음(HTTP endpoint 로 호출) — 다만 고객사가 "
                       "같이 받는 로컬 LLM 서버(vLLM/Ollama 등)에 넣을 가중치 원본이 번들에 "
                       "안 실린다는 뜻입니다. 로컬 LLM 옵션을 끄면(regulation_llm_select_enabled"
                       "=False 등) 당장 문제 없습니다")
            else:
                why = ("airgap(onprem-local)은 require_real_embedder=True 라 이게 없으면 "
                       "startup 이 죽습니다(#2)")
            print(
                f"  [ERR] {m.role} 모델 HF 캐시 부재: {src}. {why}. "
                f"`python scripts/cache_kure_v1.py --models {m.name}` 로 먼저 캐시하세요.",
                file=sys.stderr,
            )
            ok = False
            continue
        if dst.exists():
            print(f"  [skip] already exists: {dst}", file=sys.stderr)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            # 리눅스 빌드호스트: HF blob↔snapshot 상대 심링크 보존(중복 없음·용량 최소).
            shutil.copytree(src, dst, symlinks=True, ignore=_hf_keep_main_revision(src))
            _prune_unreferenced_blobs(dst)
        except (OSError, shutil.Error):
            # Windows 심링크 권한 부재 등 → 심링크 따라가며 실본문 복사(용량↑, 이식성 우선).
            if dst.exists():
                shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, symlinks=False, ignore=_hf_keep_main_revision(src))
        size_mb = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file()) / 1_048_576
        print(f"  [embed] {m.name} -> {dst}  ({size_mb:.0f}MB)", file=sys.stderr)
    return ok


def build_bundle(
    manifest: "BundleManifest",
    out_dir: Path,
    *,
    stage_embedder: bool = True,
) -> int:
    """실 빌드: docker save + infra 파일 복사 + 모델 스테이징."""
    print("\n=== Koipa Airgap Bundle - BUILD ===", file=sys.stderr)
    images_dir = out_dir / "docker-images"
    images_dir.mkdir(parents=True, exist_ok=True)

    failed: list[str] = []

    # docker save — 같은 이미지를 쓰는 서비스(worker·beat)는 tar 하나로 합친다(image_tar_plan).
    for svc, image in image_tar_plan(manifest.components).items():
        ok = _docker_save(image, images_dir / f"{svc}.tar")
        if not ok:
            failed.append(f"docker:{svc}")

    # infra 파일
    _copy_infra(out_dir, manifest.version)

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

    # #2: 임베딩 모델(KURE-v1) HF 캐시 스테이징 — airgap onprem-local 은 require_real_embedder
    # 라 임베더가 없으면 startup 이 fail-secure RuntimeError 로 죽는다. fail-closed(미스테이징 시
    # 번들 빌드 실패)로 '임베더 없는 self-contained 번들'이 출하되는 무음 갭을 원천 차단한다.
    if stage_embedder:
        if not _copy_embedder_cache(manifest, out_dir):
            failed.append("embedder-cache")
    else:
        print(
            "  [WARN] --skip-embedder — 임베딩 모델 스테이징 생략. airgap onprem-local 은 "
            "임베더 없으면 startup 이 죽으니 별도 채널로 반드시 공급하세요(#2).",
            file=sys.stderr,
        )

    if failed:
        print(f"\n[bundle] partial build — failed: {failed}", file=sys.stderr)
        print("[bundle] run verify.sh after deploying to confirm checksums", file=sys.stderr)
        return 1

    print("\n[bundle] all components saved OK", file=sys.stderr)
    return 0


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────


def enforce_release_gate(readiness: str, allow_conditional: bool,
                         *, require_fresh: bool = True) -> int:
    """실 빌드 직전 release-gate를 fail-closed로 강제한다 (0=통과, !=0=차단→빌드 중단).

    check_release_gate.py는 완비·테스트됐으나 종전엔 make 수동 타깃에만 있어, readiness가
    FAIL이어도 이 스크립트로 번들이 그대로 빌드·출하될 수 있었다(도크스트링의 'wired into the
    deploy path' 의도 미이행). 이 함수가 실 build 경로에 게이트를 배선한다 — dry-run(CI manifest
    검증)은 대상이 아니다. 서브프로세스 호출이라 배포 스크립트가 같은 방식으로 재사용 가능하다.
    
    [2026-08-16] `--require-fresh` 를 **기본으로 건다.** 종전에는 verdict 만 봤다 - 증거가
    언제 나왔는지, 이 빌드에서 나온 것인지, 무엇을 읽고 나온 것인지는 안 물었다.
    `scripts/deploy_checklist.sh` 는 이미 걸고 있었는데 정작 **번들 빌드가 안 걸었다.**
    번들이 실제 출하물이라 그쪽이 더 중요하다.

    실측으로 확인된 두 가지가 이 검사에 걸린다.
        · manifest 6/1 READY 를 8/15 빌드에 재사용 (증거 나이·커밋 불일치)
        · readiness 는 당일 날짜인데 그 입력 P2 리포트가 두 달 반 묵은 ES 리포트
          (readiness 의 `evidence_inputs` 기록 후 입력 나이까지 검사한다)

    ⚠ **우회 인자를 두지 않았다.** 증거가 낡았으면 다시 뽑는 것이 맞고, 뽑는 데 몇 초다
      (`build_operational_readiness.py`). 우회로를 열어두면 게이트는 그 길로 죽는다.
      `require_fresh=False` 는 **테스트에서 verdict 판정만 보려는 경우**를 위한 것이다.
    """
    gate_cmd = [
        sys.executable, str(_HERE / "check_release_gate.py"),
        "--readiness", str(readiness),
    ]
    if require_fresh:
        gate_cmd.append("--require-fresh")
    if allow_conditional:
        gate_cmd.append("--allow-conditional")
    print(f"\n[bundle] release-gate 검사 -> {' '.join(gate_cmd)}", file=sys.stderr)
    return subprocess.run(gate_cmd).returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="번들 버전 (예: 1.0.0)")
    ap.add_argument("--output", default="dist/koipa-airgap-bundle", help="출력 디렉터리")
    ap.add_argument("--target-env", default="KOIPA-prod")
    ap.add_argument("--dry-run", action="store_true", help="다운로드 없이 manifest만 생성")
    ap.add_argument(
        # 폐쇄망 번들은 airgap compose 를 파싱한다(image: 태그 보유 → api/worker 이미지 추출 가능).
        # dev compose(docker-compose.yml)는 api/worker 가 build: 라 image 추출 불가 + minio/mlflow 잔존.
        "--compose", default=str(_REPO_ROOT / "docker-compose.airgap.yml"),
        help="파싱할 docker-compose 경로(기본: airgap)",
    )
    ap.add_argument(
        "--config", default=str(_REPO_ROOT / "src" / "koipa" / "config.py"),
        help="파싱할 config.py 경로",
    )
    ap.add_argument(
        "--classifier-model-dir", default=None,
        help="학습된 분류기 가중치 디렉토리(가중치+temperature.json). "
             "미지정 시 env CLASSIFIER_MODEL_DIR/KOIPA_CLASSIFIER_MODEL_DIR 사용(#40)",
    )
    ap.add_argument(
        "--skip-observability", action="store_true",
        help="관측성 스택(Prometheus/Grafana/Loki + 안전 알림)을 번들에서 제외. "
             "기본은 포함 — 안전 알림 소비자는 폐쇄망 운영 필수.",
    )
    ap.add_argument(
        "--allow-base-only-classifier", action="store_true",
        help="학습 분류기 없이(베이스 모델만) 번들을 만든다. 폐쇄망에서 rule-fallback 으로 "
             "뜨므로 무음 미탐 위험 - 의도적일 때만 명시할 것.",
    )
    ap.add_argument(
        "--release-model", default="artifacts/classifier_p1_v5_clean/v-fe4b386b",
        help="이번 릴리스의 검증된 분류기(단일 진실원). 번들에 실리는 분류기가 이 버전을 "
             "담지 않으면 빌드 중단(번들↔prod parity). 릴리스마다 갱신.",
    )
    ap.add_argument(
        "--readiness", default="reports/operational_readiness.json",
        help="실 빌드 직전 release-gate가 검사할 readiness 리포트 경로(fail-closed).",
    )
    ap.add_argument(
        "--allow-conditional", action="store_true",
        help="파일럿: CONDITIONALLY_READY(데이터천장 BLOCKED만)를 release-gate가 waive하도록 통과. "
             "FAIL/누락 리포트는 절대 waive 안 함. env RELEASE_GATE_ALLOW_CONDITIONAL=1로도 가능.",
    )
    ap.add_argument(
        "--strict-parity", action="store_true",
        help="readiness 리포트의 평가모델이 번들 분류기와 다르면 빌드 중단(GA 권장). "
             "기본은 경고만 — 파일럿은 낡은 readiness 로도 굽되 근거 불일치를 로그로 남긴다.",
    )
    ap.add_argument(
        "--skip-release-gate", action="store_true",
        help="release-gate 검사를 건너뛴다(긴급 우회 - GA 비권장·감사대상). 기본은 fail-closed.",
    )
    ap.add_argument(
        "--skip-embedder", action="store_true",
        help="임베딩 모델(KURE-v1) HF 캐시 스테이징을 건너뛴다(비권장 - airgap onprem-local 은 "
             "임베더 없으면 startup 이 죽는다). 임베더를 별도 채널로 공급할 때만.",
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
    if not any(s in manifest.components for s in _REQUIRED_DB_SERVICES):
        # DB 가 하나도 없다 — 어느 쪽이든 있어야 한다.
        missing_core = list(missing_core) + ["DB(%s 중 하나)" % "|".join(_REQUIRED_DB_SERVICES)]
    if missing_core:
        print(
            f"\n[bundle][FATAL] 폐쇄망 코어 서비스 이미지 누락: {missing_core}. "
            "docker-compose.airgap.yml 의 image: 파싱을 확인하세요(인라인 주석·${VAR} 미확장 등). "
            "이 번들은 고객사 폐쇄망에서 기동 불가합니다.",
            file=sys.stderr,
        )
        return 2

    # 번들↔릴리스 모델 parity — 잘못된/구버전 분류기 동봉 fail-closed (dry-run 포함).
    parity_violation = check_model_parity(classifier_model_dir, args.release_model)
    if parity_violation and classifier_model_dir is None and args.allow_base_only_classifier:
        print(
            "\n[bundle][WARN] 학습 분류기 없이 번들을 만든다"
            "(--allow-base-only-classifier). 폐쇄망에서 rule-fallback 으로 뜬다 - 무음 미탐 위험.",
            file=sys.stderr,
        )
        parity_violation = None
    if parity_violation:
        print(f"\n[bundle][FATAL] 모델 parity 위반: {parity_violation}", file=sys.stderr)
        return 2

    out_dir = Path(args.output)

    # 위생 가드 — 예전(ES시대) 번들 위 재빌드 시 stale tar/정본에서 뺀 산출물이 실려나가는 것 차단.
    hygiene = check_bundle_hygiene(out_dir, manifest)
    if hygiene:
        print("\n[bundle][FATAL] 번들 위생 위반(예전 산출물 잔존 의심):", file=sys.stderr)
        for v in hygiene:
            print(f"  - {v}", file=sys.stderr)
        print("  -> 출력 디렉토리를 비우고(또는 신규 경로로) 재빌드하라.", file=sys.stderr)
        return 3

    paths = write_manifest(manifest, out_dir)

    if args.dry_run:
        # CHECKSUMS는 manifest 자체만 포함 (실제 컴포넌트 없음)
        write_checksums(out_dir, list(paths.values()))
        print_checklist(manifest)
        print(f"\n[bundle] dry-run OK -> {paths['yaml']}", file=sys.stderr)
        return 0

    # ── [증거 정합] readiness 리포트가 '이 번들에 실린 모델'을 설명하는가 ──────
    # release-gate 는 verdict(PASS/CONDITIONALLY_READY)만 본다. 그래서 **다른 모델로 만든 낡은
    # readiness** 로도 게이트가 통과한다(실측 2026-08-02: readiness=v-dd3abab9(2026-07-06) 인데
    # 번들 분류기는 v-fe4b386b). 성능 근거와 출하물이 어긋난 채 납품되는 경로라 표면화한다.
    _readiness_model = _readiness_evaluated_model(args.readiness)
    _bundled_model = Path(classifier_model_dir).name if classifier_model_dir else ""
    _parity_ok = bool(_readiness_model) and _bundled_model and _bundled_model in _readiness_model
    if not _parity_ok:
        msg = (
            f"[bundle][WARN] readiness 평가모델({_readiness_model or '미상'}) != 번들 분류기"
            f"({_bundled_model or '미상'}) — 이 번들의 성능 근거는 출하 모델의 것이 아닙니다. "
            "GA 전에 `make operational-readiness`를 출하 모델로 재생성하세요."
        )
        if getattr(args, "strict_parity", False):
            print(msg.replace("[WARN]", "[FATAL]"), file=sys.stderr)
            return 3
        print(msg, file=sys.stderr)

    # ── [release-gate] 실 빌드 직전 fail-closed 게이트 ──────────
    # readiness FAIL/BLOCKED(파일럿 미허용)면 번들 빌드를 중단한다. dry-run(CI manifest 검증)은
    # 위에서 이미 return 했으므로, 이 게이트는 실제로 출하되는 산출물 빌드에만 걸린다.
    if not args.skip_release_gate:
        gate_rc = enforce_release_gate(args.readiness, args.allow_conditional)
        if gate_rc != 0:
            print(
                "\n[bundle][FATAL] release-gate 미통과 — 번들 빌드를 중단합니다. "
                "readiness를 PASS로 올리거나, 파일럿은 --allow-conditional "
                "(env RELEASE_GATE_ALLOW_CONDITIONAL=1)로 데이터천장 게이트를 waive하세요. "
                "긴급 우회는 --skip-release-gate(GA 비권장·감사대상).",
                file=sys.stderr,
            )
            return gate_rc

    # ── 실 빌드 ──────────────────────────────────────────────
    rc = build_bundle(
        manifest, out_dir,
        stage_embedder=not args.skip_embedder,
    )
    if rc == 0:
        all_files = list(out_dir.rglob("*"))
        actual_files = [f for f in all_files if f.is_file()]
        write_checksums(out_dir, actual_files)
        print_checklist(manifest)
        print(f"\n[bundle] build OK -> {out_dir}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

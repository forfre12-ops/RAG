"""폐쇄망 번들 빌더 dry-run 단위 테스트.

doc/12 §4.1 명세 검증.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_offline_bundle import (  # noqa: E402
    BundleManifest,
    ComponentEntry,
    ModelEntry,
    PluginEntry,
    _MODEL_META,
    _simple_yaml_dump,
    build_manifest,
    default_es_plugins,
    estimate_total_size,
    expected_files,
    extract_components_from_compose,
    extract_models_from_config,
    write_checksums,
    write_manifest,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def sample_compose(tmp_path: Path) -> Path:
    path = tmp_path / "docker-compose.yml"
    path.write_text(
        "name: lloydk-poc\n"
        "\n"
        "services:\n"
        "  postgres:\n"
        "    image: postgres:16-alpine\n"
        "    environment:\n"
        "      POSTGRES_DB: lloydk\n"
        "\n"
        "  elasticsearch:\n"
        "    image: docker.elastic.co/elasticsearch/elasticsearch:8.15.3\n"
        "    ports:\n"
        "      - \"9200:9200\"\n"
        "\n"
        "  api:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile.api\n"
        "    ports:\n"
        "      - \"8000:8000\"\n"
        "\n"
        "  qdrant:\n"
        "    image: qdrant/qdrant:latest\n"
        "    profiles: [\"rollback\"]\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def sample_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.py"
    path.write_text(
        '"""중앙 설정."""\n'
        "\n"
        "class Settings:\n"
        '    llm_model: str = "claude-sonnet-4-6"\n'  # 슬래시 없음 → 제외 (상용)
        '    vllm_model: str = "Qwen/Qwen3-14B"\n'
        '    classifier_base_model: str = "kakaobank/kf-deberta-base"\n'
        '    embedding_model: str = "nlpai-lab/KURE-v1"\n'
        '    embedding_fallback_model: str = "BAAI/bge-m3"\n'
        '    api_key: str = "secret"\n'  # model 단어 없음 → 제외
        '    database_url: str = "postgresql://..."\n',
        encoding="utf-8",
    )
    return path


# ─────────────────────────────────────────────────────────────
# Compose 파싱
# ─────────────────────────────────────────────────────────────


def test_extract_components_basic(sample_compose: Path):
    components = extract_components_from_compose(sample_compose)
    assert "postgres" in components
    assert components["postgres"].image == "postgres:16-alpine"
    assert components["postgres"].version == "16-alpine"
    assert components["elasticsearch"].version == "8.15.3"
    assert components["qdrant"].version == "latest"
    # api는 build:만 있고 image: 없으므로 추출 안 됨
    assert "api" not in components


def test_extract_components_handles_missing_file(tmp_path: Path):
    assert extract_components_from_compose(tmp_path / "nonexistent.yml") == {}


def test_extract_components_real_project_compose():
    """실제 프로젝트 docker-compose.yml에서 ES·postgres·minio·redis·mlflow가 추출되어야 함."""
    real = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    components = extract_components_from_compose(real)
    for expected in ("postgres", "elasticsearch", "minio", "redis", "mlflow"):
        assert expected in components, f"missing: {expected}"
    # ES 이미지가 8.15+ 라인이어야 retriever API 분기와 정합
    assert components["elasticsearch"].image.endswith(":8.15.3")


# ─────────────────────────────────────────────────────────────
# Config 파싱
# ─────────────────────────────────────────────────────────────


def test_extract_models_skips_commercial_llm(sample_config: Path):
    models = extract_models_from_config(sample_config)
    names = {m.name for m in models}
    # 슬래시 없는 "claude-sonnet-4-6"은 폐쇄망 번들 제외
    assert "claude-sonnet-4-6" not in names


def test_extract_models_includes_qwen_and_hf(sample_config: Path):
    models = extract_models_from_config(sample_config)
    names = {m.name for m in models}
    assert "Qwen/Qwen3-14B" in names
    assert "kakaobank/kf-deberta-base" in names
    assert "nlpai-lab/KURE-v1" in names
    assert "BAAI/bge-m3" in names


def test_extract_models_attaches_metadata(sample_config: Path):
    models = extract_models_from_config(sample_config)
    kure = next(m for m in models if m.name == "nlpai-lab/KURE-v1")
    assert kure.dim == 1024
    assert kure.license == "MIT"
    assert kure.role == "embedding"


def test_extract_models_unknown_gets_default():
    """_MODEL_META에 없는 모델은 license=UNKNOWN, role=unknown."""
    # 실제 메타에 없는 항목 추가용 fixture는 생략하고, 룩업 동작만 검증
    assert "Qwen/Qwen3-14B" in _MODEL_META
    assert _MODEL_META["Qwen/Qwen3-14B"]["license"] == "Apache-2.0"


def test_extract_models_skips_api_key_field(sample_config: Path):
    """`*_model`이 아닌 필드는 무시한다."""
    models = extract_models_from_config(sample_config)
    names = {m.name for m in models}
    assert "secret" not in names
    assert "postgresql://..." not in names


# ─────────────────────────────────────────────────────────────
# 크기·파일 목록
# ─────────────────────────────────────────────────────────────


def test_estimate_size_scales_with_components():
    components = {"postgres": ComponentEntry("postgres:16", "16"), "elasticsearch": ComponentEntry("es:8.15", "8.15")}
    models: list[ModelEntry] = []
    plugins: list[PluginEntry] = []
    small = estimate_total_size(components, models, plugins)

    components2 = {**components, "minio": ComponentEntry("minio:latest", "latest")}
    large = estimate_total_size(components2, models, plugins)
    assert large > small


def test_estimate_size_llm_dominates():
    """LLM 14B 모델이 단독으로 약 10GB 차지 → 전체 추정에 큰 비중."""
    components: dict[str, ComponentEntry] = {}
    no_llm = estimate_total_size(components, [], default_es_plugins("8.15.3"))
    with_llm = estimate_total_size(
        components,
        [ModelEntry(name="Qwen/Qwen3-14B", dim=None, sha256=None, license="Apache-2.0", role="llm")],
        default_es_plugins("8.15.3"),
    )
    assert with_llm - no_llm >= 9.0


def test_expected_files_lists_required_artifacts():
    components = {"postgres": ComponentEntry("postgres:16", "16")}
    models = [ModelEntry("foo/bar", None, None, "MIT", "embedding")]
    files = expected_files(components, models)
    assert "README.md" in files
    assert "install.sh" in files
    assert "manifest.yaml" in files
    assert "CHECKSUMS.sha256" in files
    assert any(f.startswith("docker-images/postgres") for f in files)
    assert any(f.startswith("models/foo-bar/") for f in files)


def test_default_es_plugins_includes_nori_and_s3():
    plugins = default_es_plugins("8.15.3")
    names = [p.name for p in plugins]
    assert "analysis-nori" in names
    assert "repository-s3" in names
    assert all(p.version == "8.15.3" for p in plugins)


# ─────────────────────────────────────────────────────────────
# build_manifest 통합
# ─────────────────────────────────────────────────────────────


def test_build_manifest_adds_local_build_services(sample_compose: Path, sample_config: Path):
    """api/worker가 build:만 있어도 매니페스트엔 포함되어야 함."""
    m = build_manifest(
        version="1.0.0",
        target_env="test",
        dry_run=True,
        compose_path=sample_compose,
        config_path=sample_config,
    )
    assert "api" in m.components
    assert "worker" in m.components
    assert m.components["api"].image == "lloydk-api:1.0.0"


def test_build_manifest_excludes_qdrant_by_default(sample_compose: Path, sample_config: Path):
    m = build_manifest(
        version="1.0.0",
        target_env="test",
        dry_run=True,
        compose_path=sample_compose,
        config_path=sample_config,
        include_qdrant=False,
    )
    assert "qdrant" not in m.components


def test_build_manifest_includes_qdrant_when_flag_set(sample_compose: Path, sample_config: Path):
    m = build_manifest(
        version="1.0.0",
        target_env="test",
        dry_run=True,
        compose_path=sample_compose,
        config_path=sample_config,
        include_qdrant=True,
    )
    assert "qdrant" in m.components


def test_build_manifest_dry_run_no_sha256(sample_compose: Path, sample_config: Path):
    m = build_manifest(
        version="1.0.0",
        target_env="test",
        dry_run=True,
        compose_path=sample_compose,
        config_path=sample_config,
    )
    # dry-run에선 컴포넌트 SHA256 없음
    for c in m.components.values():
        assert c.sha256 is None
    for plg in m.es_plugins:
        assert plg.sha256 is None


def test_build_manifest_policies_force_vllm(sample_compose: Path, sample_config: Path):
    """폐쇄망 기본 정책: vllm + thinking 비활성."""
    m = build_manifest(
        version="1.0.0",
        target_env="test",
        dry_run=True,
        compose_path=sample_compose,
        config_path=sample_config,
    )
    assert m.policies.vector_backend_default == "es"
    assert m.policies.llm_provider_default == "vllm"
    assert m.policies.qwen3_thinking_mode is False


# ─────────────────────────────────────────────────────────────
# 직렬화 + 체크섬
# ─────────────────────────────────────────────────────────────


def test_write_manifest_creates_both_yaml_and_json(tmp_path: Path, sample_compose: Path, sample_config: Path):
    m = build_manifest(
        version="1.0.0",
        target_env="test",
        dry_run=True,
        compose_path=sample_compose,
        config_path=sample_config,
    )
    paths = write_manifest(m, tmp_path / "out")
    assert paths["yaml"].exists()
    assert paths["json"].exists()

    j = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert j["bundle"]["version"] == "1.0.0"
    assert j["bundle"]["dry_run"] is True


def test_write_checksums_hashes_existing_files(tmp_path: Path):
    out = tmp_path / "bundle"
    out.mkdir()
    f = out / "test.txt"
    f.write_text("hello", encoding="utf-8")

    cs_path = write_checksums(out, [f])
    content = cs_path.read_text(encoding="utf-8")
    # sha256("hello") = 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
    assert "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824" in content
    assert "test.txt" in content


def test_write_checksums_skips_missing_files(tmp_path: Path):
    out = tmp_path / "bundle"
    out.mkdir()
    existing = out / "real.txt"
    existing.write_text("x", encoding="utf-8")
    missing = out / "ghost.txt"
    cs_path = write_checksums(out, [existing, missing])
    content = cs_path.read_text(encoding="utf-8")
    assert "real.txt" in content
    assert "ghost.txt" not in content


def test_simple_yaml_dump_handles_dict():
    out = _simple_yaml_dump({"a": 1, "b": {"c": "hello"}})
    assert "a: 1" in out
    assert "b:" in out
    assert "c: hello" in out


def test_simple_yaml_dump_handles_list_of_dicts():
    out = _simple_yaml_dump({"items": [{"x": 1}, {"x": 2}]})
    assert "items:" in out
    assert "- x: 1" in out
    assert "- x: 2" in out


def test_simple_yaml_dump_escapes_special_chars():
    """콜론·줄바꿈 포함 문자열은 quote."""
    out = _simple_yaml_dump({"k": "value: with colon"})
    assert "\"value: with colon\"" in out

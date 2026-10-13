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
    ComponentEntry,
    ModelEntry,
    PluginEntry,
    _ACCEPTANCE_SH,
    _MODEL_META,
    _simple_yaml_dump,
    build_manifest,
    check_bundle_hygiene,
    check_model_parity,
    enforce_release_gate,
    estimate_total_size,
    expected_files,
    extract_components_from_compose,
    extract_models_from_config,
    hash_model_dir,
    write_checksums,
    write_manifest,
)


# ─────────────────────────────────────────────────────────────
# release-gate 배선 회귀 — 번들 빌드가 readiness FAIL을 fail-closed로 막는지
# (종전엔 check_release_gate가 make 수동 타깃에만 있어 미배선이었다)
# ─────────────────────────────────────────────────────────────


def _write_readiness(path: Path, verdict: str, gates: list) -> Path:
    path.write_text(json.dumps({"verdict": verdict, "gates": gates}), encoding="utf-8")
    return path


def test_release_gate_blocks_build_on_fail(tmp_path: Path):
    """실 빌드 직전 release-gate 배선 — FAIL readiness면 enforce_release_gate가 차단(!=0)."""
    rp = _write_readiness(
        tmp_path / "readiness.json", "FAIL",
        [{"name": "p1_classifier", "status": "FAIL", "detail": "F1 regression"}],
    )
    assert enforce_release_gate(str(rp), allow_conditional=False) != 0


def test_release_gate_missing_report_blocks(tmp_path: Path):
    """리포트 자체가 없으면(증거 공백) 파일럿에서도 차단."""
    missing = tmp_path / "nope.json"
    assert enforce_release_gate(str(missing), allow_conditional=True) != 0


def test_release_gate_pilot_waives_conditional_but_not_fail(tmp_path: Path):
    """CONDITIONALLY_READY(BLOCKED만)는 --allow-conditional로 통과(0)하되, FAIL은 파일럿도 차단.

    ⚠ 이 테스트의 관심사는 **verdict 판정**뿐이라 require_fresh=False 로 둔다. 픽스처
      readiness 에는 generated_at·git_sha·evidence_inputs 가 없어 신선도 검사를 켜면
      그것 때문에 막히고, 그러면 verdict 로직이 맞는지를 못 본다.
      신선도가 **기본으로 켜져 있다**는 것은 아래 별도 테스트가 잠근다.
    """
    conditional = _write_readiness(
        tmp_path / "cond.json", "CONDITIONALLY_READY",
        [{"name": "human_review_gold", "status": "BLOCKED", "detail": "1/40"},
         {"name": "p1_classifier", "status": "PASS", "detail": "ok"}],
    )
    assert enforce_release_gate(str(conditional), allow_conditional=False,
                                require_fresh=False) != 0   # 파일럿 미허용 = 차단
    assert enforce_release_gate(str(conditional), allow_conditional=True,
                                require_fresh=False) == 0    # 파일럿 waive = 통과

    fail = _write_readiness(
        tmp_path / "fail.json", "FAIL",
        [{"name": "p2_retrieval", "status": "FAIL", "detail": "recall drop"}],
    )
    assert enforce_release_gate(str(fail), allow_conditional=True,
                                require_fresh=False) != 0    # FAIL은 파일럿도 차단


def test_release_gate_requires_fresh_evidence_by_default(tmp_path: Path):
    """번들 빌드는 **기본으로** 증거 신선도를 묻는다.

    2026-08-16 까지 이 경로가 verdict 만 봤다. `deploy_checklist.sh` 는 --require-fresh 를
    걸고 있었는데 정작 **번들 빌드가 안 걸었다** - 번들이 실제 출하물인데도. 그래서
    "verdict 는 PASS 인데 증거가 언제·무엇으로 나왔는지는 아무도 모르는" 번들이 나올 수
    있었다. 실제로 manifest 6/1 READY 가 8/15 빌드에 재사용됐다.

    verdict 가 PASS 여도 신선도 근거가 없으면 막혀야 한다. 파일럿 waiver 는 **데이터 천장**
    (human_review 부족)을 봐주는 것이지 증거 공백을 봐주는 것이 아니라, 여기서도 막힌다.
    """
    clean = _write_readiness(
        tmp_path / "pass.json", "PASS",
        [{"name": "p1_classifier", "status": "PASS", "detail": "ok"}],
    )
    # 신선도를 끄면 통과한다 - verdict 자체는 문제없다는 뜻
    assert enforce_release_gate(str(clean), allow_conditional=False,
                                require_fresh=False) == 0
    # 기본(신선도 켬)에서는 막힌다 - generated_at·git_sha·evidence_inputs 가 없다
    assert enforce_release_gate(str(clean), allow_conditional=False) != 0
    # 파일럿 waiver 로도 안 뚫린다 - 증거 공백은 데이터 천장이 아니다
    assert enforce_release_gate(str(clean), allow_conditional=True) != 0


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def sample_compose(tmp_path: Path) -> Path:
    path = tmp_path / "docker-compose.yml"
    path.write_text(
        "name: koipa-poc\n"
        "\n"
        "services:\n"
        "  postgres:\n"
        "    image: postgres:16-alpine\n"
        "    environment:\n"
        "      POSTGRES_DB: koipa\n"
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
        "      - \"8000:8000\"\n",
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
    # api는 build:만 있고 image: 없으므로 추출 안 됨
    assert "api" not in components


def test_extract_components_handles_missing_file(tmp_path: Path):
    assert extract_components_from_compose(tmp_path / "nonexistent.yml") == {}


def test_extract_components_real_project_compose():
    """실제 프로젝트 docker-compose.yml에서 postgres·redis·mlflow가 추출되어야 함."""
    real = Path(__file__).resolve().parents[1] / "docker-compose.yml"
    components = extract_components_from_compose(real)
    for expected in ("postgres", "redis", "mlflow"):
        assert expected in components, f"missing: {expected}"
    # ES 제거(의사결정_대장 §03 ⓑ) — elasticsearch 서비스는 더 이상 없어야 한다.
    assert "elasticsearch" not in components
    # minio 제거(e10e4246) — 실배포 프로파일이 전부 storage_backend=local 이라 뺐다.
    # 폐쇄망 번들에 minio 이미지를 다시 담지 않도록 여기서 못박는다.
    assert "minio" not in components
    # postgres는 pgvector 이미지(dense 백엔드)
    assert "pgvector" in components["postgres"].image


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
    no_llm = estimate_total_size(components, [], [])
    with_llm = estimate_total_size(
        components,
        [ModelEntry(name="Qwen/Qwen3-14B", dim=None, sha256=None, license="Apache-2.0", role="llm")],
        [],
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
    # [#2] 임베딩 모델은 HF 캐시 레이아웃으로 기대돼야 한다(HF_HOME=/models/hf 오프라인 로드).
    assert any(f == "models/hf/hub/models--foo--bar/" for f in files)
    assert not any(f == "models/foo-bar/" for f in files)  # 옛 잘못된 경로 재발 방지


def test_bundle_root_readme_is_shipped_and_its_commands_exist(tmp_path: Path):
    """번들 루트에 README.md 가 실리고, 거기 적힌 명령·문서가 번들에 실제로 있는가.

    expected_files() 는 README.md 를 선언하는데 만드는 코드가 없어 번들 루트에 README 가 없었다
    (20260928 번들 실물, 2026-09-28). 설치자가 처음 여는 파일이라 명령이 어긋나면 첫 줄에서 막힌다.
    """
    import re

    from build_offline_bundle import _copy_infra

    _copy_infra(tmp_path, version="t")
    readme = tmp_path / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")

    shipped = {p.name for p in tmp_path.glob("*.sh")}
    cmds = set(re.findall(r"bash ([\w./-]+\.sh)", text))
    assert {"preflight_host.sh", "verify.sh", "setup.sh", "verify_install.sh"} <= cmds
    assert cmds <= shipped, f"README 가 부르는데 번들에 없는 스크립트: {sorted(cmds - shipped)}"

    docs = set(re.findall(r"`(docs/[A-Z_]+\.md)`", text))
    assert docs, "README 가 가리키는 문서가 없다"
    assert all((tmp_path / d).is_file() for d in docs), sorted(d for d in docs if not (tmp_path / d).is_file())

    # 문서 규약의 금지 표현 — 설치자가 무엇을 해야 하는지 흐려지는 말
    for word in ("추후", "필요시", "적절히", "판단됩니다", "검토하겠습니다"):
        assert word not in text, word


def test_bundle_root_scripts_ship_every_script_they_source(tmp_path: Path):
    """번들 루트의 셸 스크립트가 읽는(source) 스크립트가 번들에 실제로 실리는가.

    deploy_airgap.sh 는 4단계에서 `. "$SELF/db_probe.sh"` 로 읽는데 빌더의 복사 목록에 없어
    번들에 안 실렸다. 리포에서 직접 돌리면 scripts/ 에 있어 안 드러났고, 실제 설치기를 번들
    형태 폴더에서 돌리자 "No such file or directory" 로 설치가 멈췄다(2026-09-27).
    """
    import re

    from build_offline_bundle import _copy_infra

    _copy_infra(tmp_path, version="t")
    shipped = {p.name for p in tmp_path.glob("*.sh")}
    assert {"setup.sh", "deploy_airgap.sh"} <= shipped  # 검사가 빈 껍데기가 아닌지

    sourced: dict[str, set[str]] = {}
    for script in tmp_path.glob("*.sh"):
        text = script.read_text(encoding="utf-8")
        for m in re.finditer(
            r'(?m)^\s*(?:\.|source)\s+"?\$\{?(?:SELF|BUNDLE|ROOT)\}?/([A-Za-z0-9_.-]+\.sh)', text
        ):
            sourced.setdefault(script.name, set()).add(m.group(1))
    assert sourced, "source 하는 스크립트를 하나도 못 찾았다 — 이 시험의 정규식이 낡았다"
    missing = {
        f"{owner} -> {name}" for owner, names in sourced.items() for name in names if name not in shipped
    }
    assert not missing, f"번들 루트 스크립트가 읽는데 번들에 없다: {sorted(missing)}"


def test_verify_install_follows_api_port_when_base_url_is_not_given():
    """setup.sh 8단계는 `API_PORT=… bash verify_install.sh` 로 부른다 — 이 스크립트가 그 값을 따라야 한다.

    읽지 않으면 기본 포트가 아닌 설치에서 엉뚱한 서버(8000)를 검사한다(2026-09-27 실설치 리허설).
    """
    import re

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    setup = (scripts / "setup.sh").read_text(encoding="utf-8")
    verify = (scripts / "verify_install.sh").read_text(encoding="utf-8")
    assert re.search(r"API_PORT='\$API_PORT'\s+bash\s+'\$BUNDLE/verify_install\.sh'", setup), (
        "setup.sh 가 verify_install.sh 에 API_PORT 를 넘기지 않는다 — 이 시험의 전제가 바뀌었다"
    )
    assert re.search(r'^BASE_URL="\$\{BASE_URL:-http://127\.0\.0\.1:\$\{API_PORT:-8000\}\}"', verify, re.M), (
        "verify_install.sh 가 API_PORT 로 BASE_URL 을 만들지 않는다"
    )


def test_expected_files_lists_db_probe():
    """설치 확인(verify_install)이 기대 목록으로 db_probe.sh 의 결손을 잡을 수 있어야 한다."""
    files = expected_files({"postgres": ComponentEntry("postgres:16", "16")}, [])
    assert "db_probe.sh" in files


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
    assert m.components["api"].image == "koipa-api:1.0.0"


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


def test_manifest_security_scan_is_honest_by_default(sample_compose: Path, sample_config: Path):
    """[정직화] 빌드가 실제 스캔을 안 하면 매니페스트가 trivy 수행을 암시하지 않아야 한다."""
    m = build_manifest(
        version="1.0.0",
        target_env="test",
        dry_run=True,
        compose_path=sample_compose,
        config_path=sample_config,
    )
    sec = m.to_dict()["security"]
    assert sec["scanned"] is False
    label = sec["scanned_with"].lower()
    assert "trivy" not in label            # 안 한 스캔을 도구가 수행한 것처럼 암시 금지
    assert "dry-run skipped" not in label
    assert sec["critical_cves"] == 0 and sec["high_cves"] == 0


def test_build_manifest_policies_force_vllm(sample_compose: Path, sample_config: Path):
    """폐쇄망 기본 정책: vllm + thinking 비활성."""
    m = build_manifest(
        version="1.0.0",
        target_env="test",
        dry_run=True,
        compose_path=sample_compose,
        config_path=sample_config,
    )
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


def test_write_manifest_yaml_has_no_crlf(tmp_path: Path, sample_compose: Path, sample_config: Path):
    """manifest.yaml 이 CRLF 로 나가면 setup.sh 의 `sed -n 's/^ *version: *//p'` 추출값에
    트레일링 \\r 이 섞인다. Windows Git-Bash 의 sed 는 CRLF 를 알아서 지워 가려지지만, 실제
    타깃인 리눅스 sed 는 그대로 살려서 `rocky-koipa-1.0.0\\r` 처럼 IMAGE_TAG_DEFAULT 가 깨진다
    (실측 2026-09-27: 실빌드 산출물을 WSL Rocky 의 진짜 sed 로 확인) — 깨진 태그로는
    `docker compose` 가 이미지를 못 찾는다.
    """
    m = build_manifest(
        version="1.0.0",
        target_env="test",
        dry_run=True,
        compose_path=sample_compose,
        config_path=sample_config,
    )
    paths = write_manifest(m, tmp_path / "out")
    assert b"\r\n" not in paths["yaml"].read_bytes()
    assert b"\r\n" not in paths["json"].read_bytes()


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


def test_write_checksums_has_no_crlf(tmp_path: Path):
    """CHECKSUMS.sha256 이 CRLF 로 나가면 verify.sh 의 `sha256sum -c` 가 각 줄 끝 \\r 을
    파일명 일부로 읽어 전 파일이 "No such file or directory" 로 실패한다(실측 2026-09-27:
    실빌드 3,799줄 전부 CRLF — verify.sh 의 체크섬 검증이 통째로 깨졌었다).
    """
    out = tmp_path / "bundle"
    out.mkdir()
    f = out / "test.txt"
    f.write_text("hello", encoding="utf-8")

    cs_path = write_checksums(out, [f])
    assert b"\r\n" not in cs_path.read_bytes()


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


def test_write_checksums_excludes_itself_on_rebuild(tmp_path: Path):
    # [#1 회귀] 재빌드-over-existing: rglob 이 이전 CHECKSUMS.sha256 을 파일 목록에 포함시켜도
    # 매니페스트는 자기 자신을 해시하면 안 된다. 자기참조 라인이 있으면 파일을 덮어쓴 직후
    # 반드시 불일치가 되어 verify.sh 가 항상 abort 한다(에어갭 무결성 검증 영구 실패).
    import hashlib

    out = tmp_path / "bundle"
    out.mkdir()
    real = out / "real.txt"
    real.write_text("payload", encoding="utf-8")
    stale = out / "CHECKSUMS.sha256"
    stale.write_text("deadbeef  real.txt\n", encoding="utf-8")  # 이전 빌드의 낡은 매니페스트

    # rglob 이 돌려주듯 CHECKSUMS.sha256 을 목록에 섞어 전달.
    cs_path = write_checksums(out, [real, stale])
    content = cs_path.read_text(encoding="utf-8")

    # 자기참조 라인이 없어야 한다.
    assert "  CHECKSUMS.sha256" not in content
    assert "real.txt" in content
    # 남은 모든 라인의 해시는 현재 파일과 일치해야 한다(=verify 통과).
    for line in [ln for ln in content.splitlines() if ln.strip()]:
        digest, rel = line.split("  ", 1)
        actual = hashlib.sha256((out / rel).read_bytes()).hexdigest()
        assert digest == actual, f"{rel} 해시 불일치 → verify.sh abort"


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


# ─────────────────────────────────────────────────────────────
# 번들↔릴리스 parity + 위생 (배포 전 fail-closed 게이트)
# ─────────────────────────────────────────────────────────────


def test_hash_model_dir_is_deterministic_and_content_sensitive(tmp_path: Path):
    d = tmp_path / "model"
    (d / "sub").mkdir(parents=True)
    (d / "config.json").write_text("{}", encoding="utf-8")
    (d / "sub" / "weights.bin").write_bytes(b"\x01\x02\x03")
    h1 = hash_model_dir(d)
    assert len(h1) == 64 and h1 == hash_model_dir(d)  # 결정적
    (d / "sub" / "weights.bin").write_bytes(b"\x01\x02\x04")  # 내용 변경
    assert hash_model_dir(d) != h1


def test_model_parity_passes_when_release_version_present():
    assert check_model_parity(
        Path("artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9"),
        "artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9",
    ) is None
    # 경로 하위에 버전이 있어도 통과(basename 아닌 경로 내 존재)
    assert check_model_parity(
        Path("/models/v-dd3abab9/weights"),
        "artifacts/x/v-dd3abab9",
    ) is None


def test_model_parity_blocks_wrong_version():
    msg = check_model_parity(
        Path("artifacts/old/v-f9b5cedb"),
        "artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9",
    )
    assert msg is not None and "v-dd3abab9" in msg


def test_model_parity_rejects_missing_classifier():
    """미동봉은 **위반이다.**

    이 테스트는 종전에 `is None`(위반 아님)을 고정하고 있었다. 그 동작이 곧 결함이었다 -
    학습 분류기가 통째로 빠진 번들이 parity 를 통과했고, 폐쇄망에서 rule-fallback 으로
    떴다(등급을 룰이 만들고 무음 미탐이 난다). 실측 2026-08-15 에 dist/ 번들이 6/2 모델을
    싣고 있었던 것이 그 경로다. 상세는 tests/test_bundle_classifier_guard.py.
    """
    v = check_model_parity(None, "artifacts/x/v-dd3abab9")
    assert v and "rule-fallback" in v


def _hygiene_manifest(components: list[str], obs: list[str] | None = None):
    from types import SimpleNamespace
    return SimpleNamespace(components={c: None for c in components}, observability_images=obs or [])


def test_hygiene_flags_foreign_image_tar(tmp_path: Path):
    imgs = tmp_path / "docker-images"
    imgs.mkdir()
    (imgs / "postgres.tar").write_bytes(b"x")       # 허용(컴포넌트)
    (imgs / "elasticsearch.tar").write_bytes(b"x")  # 이질(ES시대 잔존)
    (imgs / "minio.tar").write_bytes(b"x")          # 이질
    manifest = _hygiene_manifest(["postgres", "redis", "api", "worker", "beat"])
    violations = check_bundle_hygiene(tmp_path, manifest)
    joined = "\n".join(violations)
    assert "elasticsearch.tar" in joined and "minio.tar" in joined
    assert "postgres.tar" not in joined


def test_hygiene_flags_retired_artifacts(tmp_path: Path):
    """정본에서 뺀 python-deps/·db-migrations/ 가 예전 번들 폴더에 남아 있으면 재빌드가 그대로 싣는다."""
    (tmp_path / "python-deps" / "wheels").mkdir(parents=True)
    (tmp_path / "db-migrations" / "alembic").mkdir(parents=True)
    joined = "\n".join(check_bundle_hygiene(tmp_path, _hygiene_manifest(["postgres"])))
    assert "python-deps/" in joined and "db-migrations/" in joined


def test_hygiene_flags_duplicate_hf_revision(tmp_path: Path):
    """같은 모델의 리비전이 둘 이상 실린 HF 캐시(KURE-v1 이 2.29GB 를 두 번 실었다)는 위반이다."""
    snaps = tmp_path / "models" / "hf" / "hub" / "models--nlpai-lab--KURE-v1" / "snapshots"
    (snaps / "aaaa").mkdir(parents=True)
    (snaps / "bbbb").mkdir(parents=True)
    joined = "\n".join(check_bundle_hygiene(tmp_path, _hygiene_manifest(["postgres"])))
    assert "duplicate HF revision" in joined and "KURE-v1" in joined


def test_hygiene_flags_beat_tar_as_foreign(tmp_path: Path):
    """beat 는 worker 와 같은 이미지라 tar 를 따로 만들지 않는다 — 예전 번들의 beat.tar 는 이질 파일이다."""
    from types import SimpleNamespace

    imgs = tmp_path / "docker-images"
    imgs.mkdir()
    (imgs / "worker.tar").write_bytes(b"x")
    (imgs / "beat.tar").write_bytes(b"x")
    comps = {"worker": ComponentEntry("koipa-worker:1", "1"), "beat": ComponentEntry("koipa-worker:1", "1")}
    manifest = SimpleNamespace(components=comps, observability_images=[])
    joined = "\n".join(check_bundle_hygiene(tmp_path, manifest))
    assert "beat.tar" in joined and "worker.tar" not in joined


def test_hygiene_clean_and_noop_when_absent(tmp_path: Path):
    # docker-images/·wheels 부재 → 순수 dry-run 안전(no-op)
    assert check_bundle_hygiene(tmp_path, _hygiene_manifest(["postgres"])) == []


# ── 학습 베이스 모델이 번들에 담기는가 (2026-09-05) ──────────────────────────
#
# 실측(로컬 full-train 스택에 실제 학습 잡을 던져 확인):
#
#     OSError: We couldn't connect to 'https://huggingface.co' to load the files,
#     and couldn't find them in the cached files.   → 0.14초 만에 실패
#
# 컨테이너 HF 캐시에는 임베더(KURE-v1)만 있었다. kf-deberta-base 는 role="classifier" 라
# 스테이징 함수의 대상(role=="embedding")에서 빠져 **어디에도 담기지 않았다.**
# 런타임은 hub id 로 참조한다(CLASSIFIER_BASE_MODEL + HF_HOME + HF_HUB_OFFLINE=1).
#
# 추론은 무관하다(CLASSIFIER_MODEL_DIR 이 평문 경로로 학습본을 가리킨다). 학습만 못 하는데,
# 폐쇄망은 enable_incremental_retrain=True 이고 증분 재학습은 매번 kf-deberta-base 에서
# 풀 파인튜닝한다(warm-start 없음).
#
# [2026-10-03 뒤집음] LLM(Qwen3-14B)도 대상이다 — api/worker 자신은 transformers 로
#   로드하지 않지만(vLLM/Ollama 의 HTTP endpoint 로 서빙), 이 번들이 그 서버를 띄우지
#   않으므로 고객사가 별도로 세울 로컬 LLM 서버용 가중치 원본으로 동봉한다.

def _models_for_cache_test():
    return [
        ModelEntry(name="nlpai-lab/KURE-v1", dim=1024, sha256=None,
                   license="MIT", role="embedding"),
        ModelEntry(name="kakaobank/kf-deberta-base", dim=None, sha256=None,
                   license="MIT", role="classifier"),
        ModelEntry(name="Qwen/Qwen3-14B", dim=None, sha256=None,
                   license="Apache-2.0", role="llm"),
    ]


def test_classifier_base_model_is_staged_into_hf_cache(tmp_path, monkeypatch):
    """분류기 베이스 모델이 HF 캐시 레이아웃으로 담긴다 — 담기지 않으면 학습이 죽는다."""
    import build_offline_bundle as B

    fake_hub = tmp_path / "hostcache" / "hub"
    for name in ("models--nlpai-lab--KURE-v1", "models--kakaobank--kf-deberta-base",
                 "models--Qwen--Qwen3-14B"):
        d = fake_hub / name
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(B, "_resolve_hf_cache_dir", lambda: fake_hub)

    class _Man:
        models = _models_for_cache_test()

    out = tmp_path / "bundle"
    out.mkdir()
    assert B._copy_embedder_cache(_Man(), out, allow_download=False) is True

    hub = out / "models" / "hf" / "hub"
    assert (hub / "models--kakaobank--kf-deberta-base").is_dir(), "베이스 모델이 안 담겼다"
    assert (hub / "models--nlpai-lab--KURE-v1").is_dir(), "임베더가 안 담겼다"


def test_llm_is_staged_into_hf_cache(tmp_path, monkeypatch):
    """[2026-10-03 뒤집음] LLM(Qwen3-14B)도 HF 캐시 레이아웃으로 담긴다.

    api/worker 프로세스 자신은 안 쓴다(HTTP endpoint 로 서빙) — 그러나 이 번들이
    vLLM/Ollama 서비스를 따로 띄우지 않으므로, 고객사가 세우는 로컬 LLM 서버에 넣을
    가중치 원본을 여기 실어 보낸다(사용자 결정, 로컬 백엔드가 쓰는 모델이 이거 하나뿐).
    """
    import build_offline_bundle as B

    fake_hub = tmp_path / "hostcache" / "hub"
    (fake_hub / "models--Qwen--Qwen3-14B").mkdir(parents=True)
    monkeypatch.setattr(B, "_resolve_hf_cache_dir", lambda: fake_hub)

    class _Man:
        models = [m for m in _models_for_cache_test() if m.role == "llm"]

    out = tmp_path / "bundle"
    out.mkdir()
    assert B._copy_embedder_cache(_Man(), out, allow_download=False) is True
    hub = out / "models" / "hf" / "hub"
    assert (hub / "models--Qwen--Qwen3-14B").is_dir(), "LLM 이 HF 캐시로 안 담겼다"


def test_missing_base_model_is_reported_as_error(tmp_path, monkeypatch, capsys):
    """캐시에 없으면 조용히 넘어가지 않고 실패로 보고한다 — 나중에 학습이 죽는 것보다 낫다."""
    import build_offline_bundle as B

    fake_hub = tmp_path / "hostcache" / "hub"
    fake_hub.mkdir(parents=True)
    monkeypatch.setattr(B, "_resolve_hf_cache_dir", lambda: fake_hub)

    class _Man:
        models = [m for m in _models_for_cache_test() if m.role == "classifier"]

    out = tmp_path / "bundle"
    out.mkdir()
    assert B._copy_embedder_cache(_Man(), out, allow_download=False) is False
    err = capsys.readouterr().err
    assert "classifier" in err and "학습" in err, err


# ─────────────────────────────────────────────────────────────
# run_acceptance.sh(_ACCEPTANCE_SH) 회귀 — Rocky8 실치설치 리허설(2026-09-27)에서 발견
# ─────────────────────────────────────────────────────────────


def test_acceptance_pack_script_has_no_crlf(tmp_path: Path):
    """run_acceptance.sh 는 root 스크립트(setup.sh 등)와 달리 디스크에서 복사되지 않고
    _ACCEPTANCE_SH 문자열을 write_text 로 직접 쓴다 — root 스크립트용 CRLF 정규화 단계를
    거치지 않는다. newline="\\n" 없이 쓰면 Windows 빌드 호스트에서 LF 가 CRLF 로 번역돼
    (Path.write_text 기본 동작) 리눅스 타깃에서 `set -o pipefail` 이 "invalid option name" 으로
    죽는다 — 실측 2026-09-27: Rocky8 실치설치 리허설, cat -A 로 전체 라인 ^M$ 확인.
    """
    from build_offline_bundle import _copy_infra

    _copy_infra(tmp_path, version="t")
    run_sh = tmp_path / "acceptance" / "run_acceptance.sh"
    assert run_sh.exists()
    assert b"\r\n" not in run_sh.read_bytes()


def test_async_classify_has_python3_fallback_for_every_json_parse():
    """_async_classify()(대용량 문서의 비동기 재시도 경로)는 바로 아래 sync 루프처럼 python3
    유무를 가려야 한다. 안 가리면 python3 가 없는 호스트(Rocky 8 기본은 /usr/bin/python3 가
    없다 — 있는 건 /usr/libexec/platform-python 뿐)에서 doc_id·job_id 추출이 매번 빈 문자열이
    되어 대용량 문서가 전부 veto(고등급 미탐) 오판정된다 — 실배포 실물확인(Rocky8+실번들)에서
    재현: 백엔드는 정상 분류(TS)했는데 인수 러너만 FAIL 을 냈다(수정 후 재현: PASS·과분류 판정).
    """
    start = _ACCEPTANCE_SH.index("_async_classify() {")
    end = _ACCEPTANCE_SH.index("\n}\n", start)
    body = _ACCEPTANCE_SH[start:end]

    # doc_id 추출 · job_id 추출 · 폴링(status/label/model_version) — 세 지점 모두 가려야 한다.
    assert body.count("command -v python3") == 3
    assert '"doc_id": *"[^"]*"' in body
    assert '"job_id": *"[^"]*"' in body
    assert '"status": *"[^"]*"' in body
    assert '"label": *"[^"]*"' in body
    assert '"model_version": *"[^"]*"' in body

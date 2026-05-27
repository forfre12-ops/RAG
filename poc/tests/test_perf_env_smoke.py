"""perf/env.py 단위 smoke 테스트 — capture_env + 서비스 핑 함수 검증."""
from __future__ import annotations


from lloydk.perf.env import (
    EnvSnapshot,
    ServiceStatus,
    capture_env,
    _git,
    _gpu,
    _ram_gb,
    _svc_es,
    _svc_minio,
    _svc_postgres,
    _svc_redis,
)


class TestServiceStatus:
    def test_defaults_unknown(self):
        s = ServiceStatus()
        assert s.postgres == "UNKNOWN"
        assert s.elasticsearch == "UNKNOWN"
        assert s.redis == "UNKNOWN"
        assert s.minio == "UNKNOWN"

    def test_explicit_values(self):
        s = ServiceStatus(postgres="UP", elasticsearch="DOWN", redis="UP", minio="UP")
        assert s.postgres == "UP"
        assert s.elasticsearch == "DOWN"


class TestEnvSnapshot:
    def test_to_dict_serializes(self):
        env = EnvSnapshot(python="3.11.0", platform="win32", cpu_count=8)
        d = env.to_dict()
        assert d["python"] == "3.11.0"
        assert d["platform"] == "win32"
        assert d["cpu_count"] == 8
        # services 필드도 dict로 직렬화
        assert "services" in d
        assert isinstance(d["services"], dict)


class TestProbeFunctions:
    """각 서비스 핑 함수가 미가용 환경에서도 안전하게 반환."""

    def test_pg_returns_string(self):
        # PG 없으면 "DOWN" 반환 (예외 안 던짐)
        result = _svc_postgres()
        assert result in ("UP", "DOWN")

    def test_es_returns_string(self):
        result = _svc_es()
        assert result in ("UP", "DOWN")

    def test_redis_returns_string(self):
        result = _svc_redis()
        assert result in ("UP", "DOWN")

    def test_minio_returns_string(self):
        result = _svc_minio()
        assert result in ("UP", "DOWN")

    def test_git_returns_string(self):
        # git 디렉토리에서는 short SHA 반환, 아니면 빈 문자열
        out = _git(["rev-parse", "--short", "HEAD"])
        assert isinstance(out, str)

    def test_git_invalid_args_no_throw(self):
        # 잘못된 git 명령 시 빈 문자열 반환 (예외 안 던짐)
        out = _git(["this-is-not-a-git-command"])
        assert isinstance(out, str)

    def test_ram_gb_returns_number(self):
        ram = _ram_gb()
        assert isinstance(ram, (int, float))
        assert ram >= 0

    def test_gpu_returns_string(self):
        gpu = _gpu()
        assert isinstance(gpu, str)
        # "N/A" 또는 GPU 이름
        assert gpu


class TestCaptureEnv:
    def test_no_probe_returns_snapshot(self):
        """probe_services=False 시 빠른 반환."""
        env = capture_env(probe_services=False, probe_pytest=False)
        assert isinstance(env, EnvSnapshot)
        assert env.python
        assert env.platform
        # probe_services=False면 모든 서비스 UNKNOWN
        assert env.services.postgres == "UNKNOWN"
        assert env.services.elasticsearch == "UNKNOWN"

    def test_with_probe_returns_known(self):
        """probe_services=True 시 UP/DOWN 둘 중 하나로 결정."""
        env = capture_env(probe_services=True, probe_pytest=False)
        # 환경에 따라 UP 또는 DOWN — UNKNOWN 아님
        assert env.services.postgres in ("UP", "DOWN")
        assert env.services.elasticsearch in ("UP", "DOWN")
        assert env.services.redis in ("UP", "DOWN")
        assert env.services.minio in ("UP", "DOWN")

    def test_custom_providers(self):
        """llm_provider/embedding_provider 등 옵션 반영."""
        env = capture_env(
            probe_services=False,
            llm_provider="anthropic",
            embedding_provider="kure-v1",
            vector_backend="es",
        )
        assert env.llm_provider == "anthropic"
        assert env.embedding_provider == "kure-v1"
        assert env.vector_backend == "es"

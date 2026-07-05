"""pytest 공통 설정 — src 경로 등록."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# J4: TestClient 환경에서는 rate-limit 기본 비활성.
# test_rate_limit.py는 fixture로 명시적 활성화 후 검증.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

# 테스트용 API key 고정 — settings.api_key="" 기본값이면 require_auth가 빈 문자열을
# falsy로 보고 즉시 401 반환하므로 로직 테스트까지 도달하지 못함.
# test_api.py의 "wrong-key" / 헤더 없음 → 401 검증은 이 값 설정 후에도 정상 동작.
os.environ.setdefault("API_KEY", "test-key")

# RBAC: 테스트는 X-Actor-Role 헤더로 역할을 자칭(HDR={"X-Actor-Role":"admin"}).
# 운영에서는 헤더 신뢰가 차단되지만(config fail-fast), 테스트는 명시적으로 opt-in.
os.environ.setdefault("API_KEY_TRUST_ACTOR_ROLE_HEADER", "true")

# 테스트 환경 표시 — assert_production_credentials, PrometheusMiddleware 등이
# poc_mode=full 환경에서도 운영 fail-fast를 skip하도록 함.
# prom_metrics._is_testing()과 동일한 패턴.
os.environ.setdefault("TESTING", "1")

# .env에 AUDIT_DISABLED=1이 있어도 테스트에서는 감사 로그를 활성화.
# audit middleware가 os.getenv로 직접 읽으므로 여기서 강제 설정.
os.environ["AUDIT_DISABLED"] = "0"

# 4-tier 프로파일 도입(commit 5a2b4e0) 이후 default lite-noapi → enable_training=False.
# 기존 test_api_routers_w3 / test_kl_integration의 train 라우터 검증은 enable_training=True
# 전제로 작성됐으므로 테스트 환경에서는 강제로 등록. test_deploy_profile의 4-프로파일
# matrix는 자기 fixture에서 monkeypatch로 override하므로 영향 없음.
os.environ.setdefault("ENABLE_TRAINING", "true")

# 벡터 백엔드 기본 inmemory — 테스트는 실 PG/ES 불요(이전 es→inmemory 폴백과 동일 효과).
# 기본을 pg로 바꾼 뒤(§03 ⓑ) pg는 지연연결이라 폴백이 없으므로, 테스트는 명시적 inmemory로.
# 실 백엔드 테스트(test_default_backend_is_pg 등)는 자체 delenv/setenv로 override.
os.environ.setdefault("VECTOR_BACKEND", "inmemory")

# 실 임베더 필수(require_real_embedder)는 운영 프로파일(onprem-local·full-train, 커밋된 .env)에서
# True다. 테스트/CI는 실 HF 모델을 항상 받지 못하므로(model_download 미표시 테스트) hash 폴백이
# 필요 — 이 프로덕션 하드닝을 중화(poc_mode fail-fast·VECTOR_BACKEND와 동일 패턴). 게이트 자체
# 검증(test_embedding_fallback_gate)은 monkeypatch로 명시 활성해 독립 검사한다.
os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")

# 수동 GA 활성 locked-eval 하드블록(deploy_gate_manual_require_locked_eval)은 하드닝 프로파일
# (onprem-local·full-train) 기본 True. 테스트는 무실데이터(locked_gold_eval 비어있음)라 이게 켜지면
# 모든 수동 활성이 막힌다 — REQUIRE_REAL_EMBEDDER와 동일하게 중화. 게이트 자체 검증은
# test_manual_activate.py의 전용 테스트가 monkeypatch로 명시 활성해 독립 수행한다.
os.environ.setdefault("DEPLOY_GATE_MANUAL_REQUIRE_LOCKED_EVAL", "false")

# force 우회 사유 필수(manual_activate_force_requires_reason)도 하드닝 프로파일 기본 True.
# 테스트의 기존 force=True(사유 없음) 경로를 깨지 않도록 중화(전용 테스트가 monkeypatch로 검증).
os.environ.setdefault("MANUAL_ACTIVATE_FORCE_REQUIRES_REASON", "false")

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _check_postgres() -> bool:
    """Postgres 5432 포트 빠른 연결 확인 (0.5초 이내)."""
    import socket
    try:
        sock = socket.create_connection(("localhost", 5432), timeout=0.5)
        sock.close()
        return True
    except OSError:
        return False


def _check_es() -> bool:
    """ES 9200 포트 빠른 연결 확인 (0.5초 이내)."""
    import socket
    try:
        sock = socket.create_connection(("localhost", 9200), timeout=0.5)
        sock.close()
        return True
    except OSError:
        return False


# 모듈 로드 시점에 한 번만 확인 (세션 전체에서 재사용)
_PG_AVAILABLE = _check_postgres()
_ES_AVAILABLE = _check_es()

# pytest marker 기반 자동 skip
# fullstack: Postgres + ES + 기타 인프라 필요
# 인프라 없을 때 TestClient 사용 테스트가 30s 타임아웃으로 블로킹되는 것 방지
def pytest_collection_modifyitems(config, items):
    """인프라 없을 때 fullstack/slow 마커 테스트 자동 skip."""
    # ES 제거(§03 PG 단일화) — fullstack 게이트는 Postgres 가용성만 본다.
    infra_up = _PG_AVAILABLE
    for item in items:
        if not infra_up:
            # TestClient를 쓰는 테스트는 infra 없으면 skip
            markers = [m.name for m in item.iter_markers()]
            if "fullstack" in markers:
                item.add_marker(pytest.mark.skip(reason="fullstack: postgres not available"))


@pytest.fixture(autouse=True)
def _restore_settings():
    """test_secrets_manager_wiring 등이 settings를 직접 변경 후 미복원하는 것을 방지.

    각 테스트 전후로 settings의 주요 필드를 저장/복원해 테스트 간 상태 오염을 차단.
    """
    from lloydk import config as config_mod

    saved = {
        "api_key": config_mod.settings.api_key,
        "minio_secret_key": config_mod.settings.minio_secret_key,
        "anthropic_api_key": config_mod.settings.anthropic_api_key,
        "poc_mode": config_mod.settings.poc_mode,
        "enable_training": config_mod.settings.enable_training,
    }
    _secrets_filled = config_mod._SECRETS_FILLED
    yield
    for k, v in saved.items():
        setattr(config_mod.settings, k, v)
    config_mod._SECRETS_FILLED = _secrets_filled

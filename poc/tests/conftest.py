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

# [2026-10-02 정정] 종전에는 여기서 AUDIT_DISABLED=0 을 강제했다("감사 로그가 꺼진 .env를
# 테스트가 물려받지 않게"). 하지만 감사 체인을 실제로 검증하는 시험들
# (test_audit_w3_d.py·test_audit_chain_wiring.py·test_secrets_manager_wiring.py 등)은
# 전부 자기 안에서 monkeypatch/patch.dict로 직접 켜고 끈다 — 이 전역 강제값에 의존하지
# 않는다. 그런데 감사로그와 무관한 나머지 테스트 전부가 이 강제값 때문에 매 HTTP 요청마다
# 실제 DB connect 를 시도했다 — 로컬에 자격증명이 안 맞는 Postgres 가 떠 있으면(예: 다른
# 컨테이너가 5432 를 쥐고 있는데 koipa/koipa_dev 를 거부) 요청 하나에 수 초가 들었다
# (test_golden_reviewer_assignment.py 67건이 19분 걸린 원인 중 하나). 기본을 끔으로 뒤집고,
# 감사 체인을 실제로 쓰는 시험은 자기 몫으로 남긴다.
os.environ.setdefault("AUDIT_DISABLED", "1")

# [2026-10-02 실측] 위 완화 이후에도 감사 체인을 실제로 켜는 시험(또는 운영에 가까운 DB
# 점검)은 여전히 이 타임아웃을 거친다. 기본(5초)은 IPv6·IPv4 순서로 두 번 소진돼 실패 시
# 10초가 들었다 — DB 가 정상이면 연결은 수십 ms 안에 끝나므로 1초로 줄여도 해가 없다
# (실패하는 경우에만 더 빨리 포기하게 한다).
os.environ.setdefault("DB_CONNECT_TIMEOUT", "1")

# 4-tier 프로파일 도입(commit 5a2b4e0) 이후 default lite-noapi → enable_training=False.
# 기존 test_api_routers_w3 / test_kl_integration의 train 라우터 검증은 enable_training=True
# 전제로 작성됐으므로 테스트 환경에서는 강제로 등록. test_deploy_profile의 4-프로파일
# matrix는 자기 fixture에서 monkeypatch로 override하므로 영향 없음.
os.environ.setdefault("ENABLE_TRAINING", "true")

# [2026-09-06] 합성 생성 라우터는 **자기 스위치**로 붙는다(enable_synthetic_generation).
# 종전에는 enable_training 에 얹혀 있어서, 학습을 끄면 FUN-003 요건 기능이 화면에서도
# API 에서도 조용히 사라졌다 — 축을 나눴다. 시험 환경은 두 라우터를 다 검증하므로 둘 다 켠다.
# 프로파일 계약(지재원 열림 · 고객사 닫힘)은 test_synth_router_availability 가 자기
# 환경변수를 세워 따로 확인하므로 이 기본값에 영향받지 않는다.
os.environ.setdefault("ENABLE_SYNTHETIC_GENERATION", "true")

# [2026-09-25] 규정 참고 표시 라우터도 자기 스위치(regulation_reference_enabled, 코드 기본 False)로 붙는다.
# 시험 환경은 라우트·콘솔 계약을 검증하므로 켠다. **코드 기본값이 False 인지**는 test_regulation_api 가 필드 기본값으로 잠근다.
os.environ.setdefault("REGULATION_REFERENCE_ENABLED", "true")

# 벡터 백엔드 기본 inmemory — 테스트는 실 PG/ES 불요(이전 es→inmemory 폴백과 동일 효과).
# 기본을 pg로 바꾼 뒤(§03 ⓑ) pg는 지연연결이라 폴백이 없으므로, 테스트는 명시적 inmemory로.
# 실 백엔드 테스트(test_default_backend_is_pg 등)는 자체 delenv/setenv로 override.
os.environ.setdefault("VECTOR_BACKEND", "inmemory")

# 실 임베더 필수(require_real_embedder)는 운영 프로파일(onprem-local·full-train, 커밋된 .env)에서
# True다. 테스트/CI는 실 HF 모델을 항상 받지 못하므로(model_download 미표시 테스트) hash 폴백이
# 필요 — 이 프로덕션 하드닝을 중화(poc_mode fail-fast·VECTOR_BACKEND와 동일 패턴). 게이트 자체
# 검증(test_embedding_fallback_gate)은 monkeypatch로 명시 활성해 독립 검사한다.
os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")

# 실 분류기 필수(require_real_classifier)도 하드닝 프로파일(onprem-local·full-train) 기본 True.
# 테스트/CI는 학습 가중치 없이 rule-fallback 으로 도는 경우가 많아(무실데이터) 이게 켜지면 warmup
# 이 차단된다 — REQUIRE_REAL_EMBEDDER 와 동일 패턴으로 중화. 게이트 자체 검증은 전용 테스트가
# monkeypatch 로 명시 활성해 독립 수행한다(test_classifier_fallback_gate).
os.environ.setdefault("REQUIRE_REAL_CLASSIFIER", "false")

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

# conftest 는 tests/ 가 sys.path 에 오르기 **전에** 로드되므로 여기서 직접 넣는다.
# 이걸 안 하면 아래 _pg_probe 임포트가 ModuleNotFoundError 로 죽는다.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _check_postgres() -> bool:
    """DB 가용성 — 판정은 _pg_probe 한 곳에만 둔다(같은 검사를 복제하지 않는다).

    (함수 이름은 호출부 호환으로 유지한다.)
    """
    from _pg_probe import postgres_available

    return postgres_available()


# 모듈 로드 시점에 한 번만 확인 (세션 전체에서 재사용)
_PG_AVAILABLE = _check_postgres()

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
                # [2026-09-05] 메시지에 실제 엔드포인트를 싣는다. "postgres not available"
                # 만 뜨면 DB 가 떠 있는데도 왜 건너뛰는지 알 수 없다
                # (실측: 기본 DB 를 바꿨을 때 fullstack 52건이 조용히 skip 됐다).
                from _pg_probe import pg_endpoint  # noqa: PLC0415

                _h, _p = pg_endpoint()
                item.add_marker(pytest.mark.skip(
                    reason=f"fullstack: DB 접속 불가 {_h}:{_p} (DATABASE_URL 또는 설정 확인)"
                ))


@pytest.fixture(autouse=True)
def _restore_settings():
    """test_secrets_manager_wiring 등이 settings를 직접 변경 후 미복원하는 것을 방지.

    각 테스트 전후로 settings의 주요 필드를 저장/복원해 테스트 간 상태 오염을 차단.
    """
    from koipa import config as config_mod

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


@pytest.fixture(autouse=True)
def _reset_regulation_runtime_toggle():
    """규정 참고 표시의 콘솔 켬/끔 스위치(redis 키)는 프로세스 전역이라 한 시험이 켜고 안 지우면
    같은 pytest 프로세스에서 도는 무관한 다른 시험까지 값을 물려받는다(실측 2026-09-27 —
    test_regulation_api 의 PUT 역할 시험이 llm-select-toggle 을 켠 채 남겨 test_regulation_service_db·
    test_regulation_service_llm 여러 건이 엉뚱하게 실패했다). redis 가 없는 환경에서도(단위 시험)
    조용히 넘어가야 하므로 예외는 삼킨다 — 이 픽스처의 목적은 청소이지 redis 가용성 검증이 아니다.
    """
    def _clear():
        try:
            from koipa.regulation import runtime_toggle
            c = runtime_toggle._client()
            c.delete(runtime_toggle._KEY, runtime_toggle._LLM_KEY)
        except Exception:  # noqa: BLE001 — redis 미가용 환경도 시험은 계속 돈다
            pass
    _clear()
    yield
    _clear()

"""pytest 공통 설정 — src 경로 등록."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# J4: TestClient 환경에서는 rate-limit 기본 비활성.
# test_rate_limit.py는 fixture로 명시적 활성화 후 검증.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

# .env에 AUDIT_DISABLED=1이 있어도 테스트에서는 감사 로그를 활성화.
# audit middleware가 os.getenv로 직접 읽으므로 여기서 강제 설정.
os.environ["AUDIT_DISABLED"] = "0"

# 4-tier 프로파일 도입(commit 5a2b4e0) 이후 default lite-noapi → enable_training=False.
# 기존 test_api_routers_w3 / test_kl_integration의 train 라우터 검증은 enable_training=True
# 전제로 작성됐으므로 테스트 환경에서는 강제로 등록. test_deploy_profile의 4-프로파일
# matrix는 자기 fixture에서 monkeypatch로 override하므로 영향 없음.
os.environ.setdefault("ENABLE_TRAINING", "true")

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


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

"""pytest 공통 설정 — src 경로 등록."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# J4: TestClient 환경에서는 rate-limit 기본 비활성.
# test_rate_limit.py는 fixture로 명시적 활성화 후 검증.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

# 4-tier 프로파일 도입(commit 5a2b4e0) 이후 default lite-noapi → enable_training=False.
# 기존 test_api_routers_w3 / test_kl_integration의 train 라우터 검증은 enable_training=True
# 전제로 작성됐으므로 테스트 환경에서는 강제로 등록. test_deploy_profile의 4-프로파일
# matrix는 자기 fixture에서 monkeypatch로 override하므로 영향 없음.
os.environ.setdefault("ENABLE_TRAINING", "true")

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

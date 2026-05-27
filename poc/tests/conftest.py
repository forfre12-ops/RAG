"""pytest 공통 설정 — src 경로 등록."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# J4: TestClient 환경에서는 rate-limit 기본 비활성.
# test_rate_limit.py는 fixture로 명시적 활성화 후 검증.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

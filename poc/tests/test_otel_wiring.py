"""D2: OpenTelemetry setup_tracing wiring 검증.

운영시: OTEL_EXPORTER_OTLP_ENDPOINT 설정 + opentelemetry-* 설치
dryrun/CI: 둘 다 부재 → silent no-op, False 반환.
"""

from __future__ import annotations

from koipa.obs.otel import setup_tracing


def test_setup_tracing_no_endpoint_returns_false(monkeypatch):
    """endpoint env 미설정 → False (no-op)."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert setup_tracing() is False


def test_setup_tracing_no_otel_package_returns_false(monkeypatch):
    """opentelemetry 패키지 import 실패 → False."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tempo:4317")

    # opentelemetry.* 전체를 None으로 → ImportError 강제
    import sys
    saved = {k: v for k, v in sys.modules.items() if k.startswith("opentelemetry")}
    for k in list(saved):
        sys.modules.pop(k, None)
    sys.modules["opentelemetry"] = None  # type: ignore[assignment]
    try:
        assert setup_tracing() is False
    finally:
        sys.modules.pop("opentelemetry", None)
        for k, v in saved.items():
            sys.modules[k] = v


def test_app_lifespan_imports_setup_tracing():
    """app.py 소스에 setup_tracing import + 호출이 들어가 있는지 정적 확인.

    lifespan E2E 통합은 ML 모델 로드로 무거우니, 정적 wiring 존재만 확인.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "koipa" / "api" / "app.py"
    text = src.read_text(encoding="utf-8")
    assert "from koipa.obs.otel import setup_tracing" in text
    assert "setup_tracing(app)" in text

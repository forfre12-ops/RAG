"""D2: OpenTelemetry setup_tracing wiring 검증.

운영시: OTEL_EXPORTER_OTLP_ENDPOINT 설정 + opentelemetry-* 설치
dryrun/CI: 둘 다 부재 → silent no-op, False 반환.
"""

from __future__ import annotations

from koipa.obs.otel import redact_server_request_span, safe_span_attributes, setup_tracing


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


def test_manual_span_attributes_are_allowlisted_and_never_include_document_text():
    attrs = safe_span_attributes(
        {
            "koipa.has_content": True,
            "model_version": "candidate-v1",
            "document_text": "customer secret must not leave this process",
            "prompt": "classify this private conversation",
            "unknown_future_field": "also not exported until reviewed",
        }
    )

    assert attrs == {"koipa.has_content": True, "model_version": "candidate-v1"}


def test_server_span_redacts_url_query_and_auth_headers():
    class Span:
        def __init__(self):
            self.attrs = {}

        def set_attribute(self, key, value):
            self.attrs[key] = value

    span = Span()
    redact_server_request_span(span, {"query_string": b"content=private"})

    assert span.attrs["http.url"] == "[redacted]"
    assert span.attrs["url.query"] == "[redacted]"
    assert span.attrs["http.request.header.authorization"] == "[redacted]"


def test_deep_automatic_instrumentation_is_explicitly_opt_in():
    from koipa.obs import otel

    assert otel._deep_instrumentation_enabled() is False

"""Tests for kairos/telemetry.py — OpenTelemetry tracing middleware.

Uses OTel's own InMemorySpanExporter (no real collector, no network) to
verify spans are actually created with the right attributes, and verifies
configure_tracing()'s no-op path when OTEL_EXPORTER_OTLP_ENDPOINT is unset
— the state every other test in this suite runs under, so this also
guards against tracing accidentally becoming non-optional.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import kairos.telemetry as telemetry_module
from kairos.telemetry import annotate_current_span, configure_tracing, install_tracing


@pytest.fixture()
def traced_app(monkeypatch):
    """A minimal app wired to an in-memory exporter, bypassing
    configure_tracing()'s env-var gate so spans are captured deterministically."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    test_tracer = trace.get_tracer(__name__, tracer_provider=provider)
    monkeypatch.setattr(telemetry_module, "_tracer", test_tracer)

    app = FastAPI()

    @app.middleware("http")
    async def _trace_request(request, call_next):
        # Mirrors install_tracing()'s own middleware body exactly, but
        # against the monkeypatched module-level _tracer above — calling
        # install_tracing(app) itself would re-run configure_tracing() and
        # potentially clobber the global tracer provider for the rest of
        # the test process.
        import time

        with telemetry_module._tracer.start_as_current_span(
            f"{request.method} {request.url.path}",
            attributes={"http.method": request.method, "http.route": request.url.path},
        ) as span:
            start = time.perf_counter()
            response = await call_next(request)
            span.set_attribute("http.status_code", response.status_code)
            span.set_attribute("http.duration_ms", round((time.perf_counter() - start) * 1000, 2))
            return response

    @app.get("/ping")
    def ping():
        annotate_current_span(**{"kairos.custom": "value", "kairos.count": 3})
        return {"ok": True}

    return app, exporter


class TestTracingMiddleware:
    def test_request_produces_a_span_with_http_attributes(self, traced_app):
        app, exporter = traced_app
        TestClient(app).get("/ping")

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "GET /ping"
        assert span.attributes["http.method"] == "GET"
        assert span.attributes["http.route"] == "/ping"
        assert span.attributes["http.status_code"] == 200
        assert span.attributes["http.duration_ms"] >= 0

    def test_annotate_current_span_adds_business_attributes(self, traced_app):
        app, exporter = traced_app
        TestClient(app).get("/ping")

        span = exporter.get_finished_spans()[0]
        assert span.attributes["kairos.custom"] == "value"
        assert span.attributes["kairos.count"] == 3

    def test_annotate_current_span_outside_a_span_does_not_raise(self):
        # No active span at all (not even a no-op one from middleware) —
        # get_current_span() returns OTel's INVALID_SPAN, and set_attribute
        # on it must be a safe no-op, not an error.
        annotate_current_span(**{"kairos.whatever": 1})


class TestConfigureTracingNoOp:
    def test_no_endpoint_configured_does_not_raise_or_install_a_provider(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        current_provider = trace.get_tracer_provider()
        configure_tracing()
        # Calling configure_tracing() with no endpoint must leave whatever
        # tracer provider was already active untouched.
        assert trace.get_tracer_provider() is current_provider

    def test_install_tracing_works_without_a_configured_endpoint(self, monkeypatch):
        # The real path every other test's `client` fixture exercises:
        # install_tracing(app) must not raise or require network access
        # when no collector is configured.
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        app = FastAPI()
        install_tracing(app)

        @app.get("/ping")
        def ping():
            return {"ok": True}

        resp = TestClient(app).get("/ping")
        assert resp.status_code == 200

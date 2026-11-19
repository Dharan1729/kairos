"""OpenTelemetry distributed tracing. Genuinely opt-in: with no
OTEL_EXPORTER_OTLP_ENDPOINT set, no SDK TracerProvider is installed and
every trace.get_tracer(...) call in this process resolves to OTel's own
no-op default — zero network calls, zero collector dependency, so every
existing test and every deployment without a collector runs exactly as it
did before this module existed.

One span per HTTP request (method + route, status, duration) via
middleware, covering every endpoint uniformly. assemble_context and
search_knowledge additionally enrich their own request's span with the
same cache/latency signals already computed for AuditLedger/dashboard.py
(kairos/dashboard.py) — the same numbers, just also visible in a trace
alongside whatever else a request touched (useful for exactly the
question dashboard.py's aggregates can't answer: "why was *this*
request slow?").
"""

import os
import time

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_SERVICE_NAME = "kairos"
_tracer = trace.get_tracer(_SERVICE_NAME)  # no-op until/unless configure_tracing() installs a real provider


def configure_tracing() -> None:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return
    # Local import: the OTLP HTTP exporter pulls in `requests`-style HTTP
    # machinery that's only worth importing when tracing is actually
    # enabled, not on every process start.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": _SERVICE_NAME}))
    # No endpoint= passed: OTLPSpanExporter() reads OTEL_EXPORTER_OTLP_ENDPOINT
    # itself and correctly appends the /v1/traces path per the OTLP spec —
    # passing endpoint=endpoint here instead would send spans straight to
    # the bare collector root (e.g. http://collector:4318 with no path),
    # which most collectors 404 on silently in the background export thread.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    global _tracer
    _tracer = trace.get_tracer(_SERVICE_NAME)


def install_tracing(app) -> None:
    configure_tracing()

    @app.middleware("http")
    async def _trace_request(request, call_next):
        with _tracer.start_as_current_span(
            f"{request.method} {request.url.path}",
            attributes={"http.method": request.method, "http.route": request.url.path},
        ) as span:
            start = time.perf_counter()
            response = await call_next(request)
            span.set_attribute("http.status_code", response.status_code)
            span.set_attribute("http.duration_ms", round((time.perf_counter() - start) * 1000, 2))
            return response


def annotate_current_span(**attributes) -> None:
    """Adds business-level attributes (cache hit, tokens used, ...) to
    whichever span the request middleware above already opened. A no-op
    call on a no-op span when tracing isn't configured — safe to call
    unconditionally from request handlers without checking whether
    tracing is enabled first."""
    span = trace.get_current_span()
    for key, value in attributes.items():
        span.set_attribute(key, value)

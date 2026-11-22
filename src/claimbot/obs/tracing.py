"""OpenTelemetry setup. Spans are no-ops unless an OTLP endpoint is configured."""

from __future__ import annotations

_configured = False


def configure_tracing(endpoint: str | None, service_name: str = "claimbot") -> None:
    global _configured
    if not endpoint or _configured:
        return
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    _configured = True

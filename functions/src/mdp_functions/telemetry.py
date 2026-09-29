"""Structured logs and optional OTLP, with no network exporter by default."""

import logging

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from mdp_functions.settings import Settings


def configure(settings: Settings) -> None:
    logging.basicConfig(level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    if settings.otlp_endpoint:
        provider = TracerProvider()
        headers = dict(
            item.split("=", 1)
            for item in settings.otlp_headers.split(",")
            if "=" in item
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otlp_endpoint, headers=headers)
            )
        )
        trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()


def trace_url(settings: Settings, trace_id: str) -> str:
    return settings.trace_url_template.format(trace_id=trace_id)

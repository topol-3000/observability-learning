"""OpenTelemetry tracing setup and safe trace-correlation helpers."""

import traceback
from dataclasses import dataclass

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from opentelemetry.trace import Status, StatusCode, Tracer

from observability_demo.logging import (
    DEPLOYMENT_ENVIRONMENT,
    SERVICE_INSTANCE_ID,
    SERVICE_NAME,
    SERVICE_NAMESPACE,
    SERVICE_VERSION,
)
from observability_demo.settings import TelemetrySettings

INSTRUMENTATION_SCOPE = "observability_demo"
HEALTH_EXCLUDED_URLS = "health/live,health/ready"


def tracing_enabled_from_environment() -> bool:
    """Enable export only when an OTLP endpoint is explicitly configured."""
    return TelemetrySettings().tracing_enabled


def service_resource() -> Resource:
    """Return the stable resource identity shared by application traces and logs."""
    return Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.namespace": SERVICE_NAMESPACE,
            "service.version": SERVICE_VERSION,
            "service.instance.id": SERVICE_INSTANCE_ID,
            "deployment.environment.name": DEPLOYMENT_ENVIRONMENT,
        }
    )


@dataclass(slots=True)
class TraceRuntime:
    """Own the tracing objects that belong to one application replica."""

    tracer: Tracer
    provider: TracerProvider | None = None
    _shutdown: bool = False

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    def shutdown(self) -> None:
        """Flush and stop this replica's provider exactly once."""
        if self.provider is not None and not self._shutdown:
            self._shutdown = True
            self.provider.shutdown()


def create_trace_runtime(
    *,
    exporter: SpanExporter | None = None,
    enabled: bool | None = None,
    batch: bool = True,
) -> TraceRuntime:
    """Create an isolated provider, or a no-op tracer when tracing is disabled."""
    tracing_enabled = tracing_enabled_from_environment() if enabled is None else enabled
    if not tracing_enabled:
        tracer = trace.NoOpTracerProvider().get_tracer(INSTRUMENTATION_SCOPE)
        return TraceRuntime(tracer=tracer)

    provider = TracerProvider(
        resource=service_resource(),
        sampler=ParentBased(root=ALWAYS_ON),
        shutdown_on_exit=False,
    )
    span_exporter = exporter if exporter is not None else OTLPSpanExporter()
    processor = (
        BatchSpanProcessor(span_exporter)
        if batch
        else SimpleSpanProcessor(span_exporter)
    )
    provider.add_span_processor(processor)
    return TraceRuntime(
        tracer=provider.get_tracer(INSTRUMENTATION_SCOPE, SERVICE_VERSION),
        provider=provider,
    )


def instrument_fastapi(application: FastAPI, runtime: TraceRuntime) -> None:
    """Create low-noise HTTP server spans using this replica's provider."""
    if runtime.provider is None:
        return
    FastAPIInstrumentor.instrument_app(
        application,
        tracer_provider=runtime.provider,
        excluded_urls=HEALTH_EXCLUDED_URLS,
        exclude_spans=["receive", "send"],
    )


def current_trace_ids() -> tuple[str, str] | None:
    """Return lowercase IDs only for a valid, sampled current span."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid or not span_context.trace_flags.sampled:
        return None
    return f"{span_context.trace_id:032x}", f"{span_context.span_id:016x}"


def mark_current_span_failed(exception: Exception | None = None) -> None:
    """Mark a server span failed without recording exception messages or values."""
    span = trace.get_current_span()
    if not span.is_recording():
        return
    span.set_status(Status(StatusCode.ERROR))
    if exception is not None:
        attributes = {
            "exception.type": (
                f"{type(exception).__module__}.{type(exception).__qualname__}"
            )
        }
        if exception.__traceback__ is not None:
            attributes["exception.stacktrace"] = (
                "Traceback (most recent call last):\n"
                + "".join(traceback.format_tb(exception.__traceback__))
            ).rstrip()
        span.add_event(
            "exception",
            attributes=attributes,
        )

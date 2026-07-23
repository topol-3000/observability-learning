"""OpenTelemetry metrics setup and bounded application instruments."""

from dataclasses import dataclass
from typing import Any

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource

from observability_demo.logging import SERVICE_VERSION
from observability_demo.settings import TelemetrySettings
from observability_demo.tracing import INSTRUMENTATION_SCOPE, service_resource


def metrics_enabled_from_environment() -> bool:
    """Enable metric export only when an OTLP endpoint is explicitly configured."""
    return TelemetrySettings().metrics_enabled


@dataclass(slots=True)
class MetricsRuntime:
    """Own the metric provider and reviewed instruments for one API replica."""

    meter: metrics.Meter
    http_request_count: Any
    http_request_duration: Any
    http_active_requests: Any
    work_count: Any
    work_duration: Any
    provider: MeterProvider | None = None
    _shutdown: bool = False

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    def record_http_started(self, method: str) -> None:
        """Increase the in-flight count using its available bounded dimension."""
        self.http_active_requests.add(1, {"http.request.method": method})

    def record_http_completed(
        self,
        duration_seconds: float,
        attributes: dict[str, str | int],
    ) -> None:
        """Record one completed HTTP operation and release its in-flight count."""
        self.http_request_count.add(1, attributes)
        self.http_request_duration.record(duration_seconds, attributes)
        self.http_active_requests.add(
            -1,
            {"http.request.method": str(attributes["http.request.method"])},
        )

    def record_work_completed(self, duration_seconds: float, outcome: str) -> None:
        """Record a completed bounded simulated-work operation."""
        attributes = {"demo.work.outcome": outcome}
        self.work_count.add(1, attributes)
        self.work_duration.record(duration_seconds, attributes)

    def shutdown(self) -> None:
        """Flush and stop this replica's metric provider exactly once."""
        if self.provider is not None and not self._shutdown:
            self._shutdown = True
            self.provider.force_flush()
            self.provider.shutdown()


def no_op_metrics_runtime() -> MetricsRuntime:
    """Create no-op instruments for tests and explicitly disabled telemetry."""
    meter = metrics.NoOpMeterProvider().get_meter(
        INSTRUMENTATION_SCOPE,
        SERVICE_VERSION,
    )
    return MetricsRuntime(
        meter=meter,
        http_request_count=meter.create_counter("demo.http.server.request.count"),
        http_request_duration=meter.create_histogram(
            "demo.http.server.request.duration"
        ),
        http_active_requests=meter.create_up_down_counter(
            "demo.http.server.active_requests"
        ),
        work_count=meter.create_counter("demo.work.count"),
        work_duration=meter.create_histogram("demo.work.duration"),
    )


def create_metrics_runtime(
    *,
    reader: MetricReader | None = None,
    enabled: bool | None = None,
    resource: Resource | None = None,
) -> MetricsRuntime:
    """Create an isolated OTLP metric provider or a no-op runtime."""
    metrics_enabled = metrics_enabled_from_environment() if enabled is None else enabled
    if not metrics_enabled:
        return no_op_metrics_runtime()

    metric_reader = reader
    if metric_reader is None:
        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    provider = MeterProvider(
        resource=service_resource() if resource is None else resource,
        metric_readers=[metric_reader],
        shutdown_on_exit=False,
    )
    meter = provider.get_meter(INSTRUMENTATION_SCOPE, SERVICE_VERSION)
    return MetricsRuntime(
        meter=meter,
        http_request_count=meter.create_counter(
            "demo.http.server.request.count",
            unit="{request}",
            description="Completed eligible HTTP server requests.",
        ),
        http_request_duration=meter.create_histogram(
            "demo.http.server.request.duration",
            unit="s",
            description="Duration of eligible HTTP server requests.",
        ),
        http_active_requests=meter.create_up_down_counter(
            "demo.http.server.active_requests",
            unit="{request}",
            description="Current eligible HTTP server requests in progress.",
        ),
        work_count=meter.create_counter(
            "demo.work.count",
            unit="{operation}",
            description="Completed simulated work operations.",
        ),
        work_duration=meter.create_histogram(
            "demo.work.duration",
            unit="s",
            description="Duration of simulated work operations.",
        ),
        provider=provider,
    )

"""Typed environment-backed settings grouped by their consumers."""

from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvironmentSettings(BaseSettings):
    """Base configuration shared by all environment-backed settings models."""

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)


class ApplicationSettings(EnvironmentSettings):
    """Settings that describe the application itself."""

    version: str = Field(default="0.1.0", validation_alias="APP_VERSION")


class ServiceSettings(EnvironmentSettings):
    """Settings that identify this service in logs and telemetry."""

    name: str = Field(
        default="observability-demo-api",
        validation_alias="OTEL_SERVICE_NAME",
    )
    namespace: str = Field(
        default="learning", validation_alias="OTEL_SERVICE_NAMESPACE"
    )
    deployment_environment: str = Field(
        default="local",
        validation_alias="DEPLOYMENT_ENVIRONMENT",
    )
    instance_id: str = Field(
        default_factory=lambda: str(uuid4()),
        validation_alias="SERVICE_INSTANCE_ID",
    )


class TelemetrySettings(EnvironmentSettings):
    """Settings controlling OTLP trace and metric export."""

    sdk_disabled: bool = Field(default=False, validation_alias="OTEL_SDK_DISABLED")
    otlp_endpoint: str | None = Field(
        default=None,
        validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT",
    )
    traces_endpoint: str | None = Field(
        default=None,
        validation_alias="OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    )
    metrics_endpoint: str | None = Field(
        default=None,
        validation_alias="OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    )

    @property
    def tracing_enabled(self) -> bool:
        """Whether traces have an explicitly configured export endpoint."""
        return not self.sdk_disabled and bool(
            self.traces_endpoint or self.otlp_endpoint
        )

    @property
    def metrics_enabled(self) -> bool:
        """Whether metrics have an explicitly configured export endpoint."""
        return not self.sdk_disabled and bool(
            self.metrics_endpoint or self.otlp_endpoint
        )


class DistributionSmokeSettings(EnvironmentSettings):
    """Settings for the load-balancer distribution smoke check."""

    url: str = Field(
        default="http://traefik:8080/debug/instance",
        validation_alias="SMOKE_URL",
    )
    expected_replicas: int = Field(default=4, validation_alias="EXPECTED_REPLICAS")
    deadline_seconds: float = Field(
        default=30, validation_alias="SMOKE_DEADLINE_SECONDS"
    )


class TraceSmokeSettings(EnvironmentSettings):
    """Settings for the end-to-end trace smoke check."""

    api_url: str = Field(
        default="http://traefik:8080/work?units=2",
        validation_alias="SMOKE_URL",
    )
    tempo_url: str = Field(default="http://tempo:3200", validation_alias="TEMPO_URL")
    expected_replicas: int = Field(default=4, validation_alias="EXPECTED_REPLICAS")
    deadline_seconds: float = Field(
        default=90, validation_alias="SMOKE_DEADLINE_SECONDS"
    )


class MetricsSmokeSettings(EnvironmentSettings):
    """Settings for the metrics ingestion smoke check."""

    api_url: str = Field(default="http://traefik:8080", validation_alias="SMOKE_URL")
    prometheus_url: str = Field(
        default="http://prometheus:9090",
        validation_alias="PROMETHEUS_URL",
    )
    deadline_seconds: float = Field(
        default=120,
        validation_alias="METRICS_SMOKE_DEADLINE_SECONDS",
    )


class LogsSmokeSettings(EnvironmentSettings):
    """Settings for the Loki and Alloy ingestion smoke check."""

    api_url: str = Field(default="http://traefik:8080", validation_alias="SMOKE_URL")
    loki_url: str = Field(default="http://loki:3100", validation_alias="LOKI_URL")
    expected_replicas: int = Field(default=4, validation_alias="EXPECTED_REPLICAS")
    deadline_seconds: float = Field(
        default=90,
        validation_alias="LOGS_SMOKE_DEADLINE_SECONDS",
    )


class GrafanaSmokeSettings(EnvironmentSettings):
    """Settings for Grafana provisioning and correlation checks."""

    api_url: str = Field(default="http://traefik:8080", validation_alias="SMOKE_URL")
    grafana_url: str = Field(
        default="http://grafana:3000",
        validation_alias="GRAFANA_URL",
    )
    deadline_seconds: float = Field(
        default=150,
        validation_alias="GRAFANA_SMOKE_DEADLINE_SECONDS",
    )

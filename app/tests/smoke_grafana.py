"""Verify provisioned Grafana data sources, dashboard, and signal correlation."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from uuid import uuid4

from observability_demo.settings import GrafanaSmokeSettings

settings = GrafanaSmokeSettings()
API_URL = settings.api_url
GRAFANA_URL = settings.grafana_url
DEADLINE_SECONDS = settings.deadline_seconds
DASHBOARD_UID = "observability-demo-service"
EXPECTED_DATASOURCES = {"prometheus", "loki", "tempo"}
EXPECTED_PANELS = {
    "External readiness",
    "Healthy backends",
    "In-flight requests",
    "Telemetry targets",
    "Application request rate",
    "Application server-error ratio",
    "Application duration percentiles",
    "Edge vs application p95",
    "Request completions by replica",
    "Recent application errors",
    "Recent slow or error traces",
    "Telemetry pipeline failures",
}


def get_json(url: str) -> dict[str, Any]:
    """Fetch one JSON object and include response context in failures."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        raise RuntimeError(f"GET {url} returned HTTP {error.code}: {body}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"GET {url} did not return a JSON object")
    return payload


def send_request(
    path: str,
    *,
    request_id: str,
    trace_id: str,
    expected_status: int,
) -> None:
    """Send a request with known correlation IDs through Traefik."""
    parent_id = uuid4().hex[:16]
    request = urllib.request.Request(
        f"{API_URL}{path}",
        headers={
            "Connection": "close",
            "Traceparent": f"00-{trace_id}-{parent_id}-01",
            "X-Request-ID": request_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            status = response.status
            returned_request_id = response.headers.get("X-Request-ID")
    except urllib.error.HTTPError as error:
        status = error.code
        returned_request_id = error.headers.get("X-Request-ID")
    if status != expected_status:
        raise RuntimeError(f"{path} returned HTTP {status}, expected {expected_status}")
    if returned_request_id != request_id:
        raise RuntimeError(f"{path} did not preserve its request ID")


def datasource(uid: str) -> dict[str, Any]:
    """Return a provisioned data source through Grafana's authenticated API."""
    return get_json(f"{GRAFANA_URL}/api/datasources/uid/{uid}")


def verify_datasources() -> None:
    """Require stable UIDs and both directions of configured correlation."""
    prometheus = datasource("prometheus")
    loki = datasource("loki")
    tempo = datasource("tempo")

    exemplar_links = prometheus.get("jsonData", {}).get(
        "exemplarTraceIdDestinations",
        [],
    )
    if not any(
        link.get("name") == "trace_id" and link.get("datasourceUid") == "tempo"
        for link in exemplar_links
    ):
        raise AssertionError("Prometheus trace_id exemplars are not linked to Tempo")

    derived_fields = loki.get("jsonData", {}).get("derivedFields", [])
    if not any(
        field.get("name") == "TraceID"
        and field.get("datasourceUid") == "tempo"
        and field.get("url") == "${__value.raw}"
        for field in derived_fields
    ):
        raise AssertionError("Loki TraceID derived field is not linked to Tempo")

    tempo_json = tempo.get("jsonData", {})
    traces_to_logs = tempo_json.get("tracesToLogsV2", {})
    if (
        traces_to_logs.get("datasourceUid") != "loki"
        or not traces_to_logs.get("filterByTraceID")
        or traces_to_logs.get("filterBySpanID")
    ):
        raise AssertionError("Tempo trace-to-logs correlation is incomplete")
    tags = traces_to_logs.get("tags", [])
    if {"key": "service.name", "value": "service"} not in tags:
        raise AssertionError("Tempo service.name is not mapped to the Loki label")

    traces_to_metrics = tempo_json.get("tracesToMetrics", {})
    if traces_to_metrics.get("datasourceUid") != "prometheus":
        raise AssertionError("Tempo trace-to-metrics is not linked to Prometheus")
    queries = traces_to_metrics.get("queries", [])
    if {query.get("name") for query in queries} != {
        "Request rate",
        "Error rate",
        "p95 duration",
    }:
        raise AssertionError("Tempo trace-to-metrics queries are incomplete")


def panel_datasource_uids(panel: dict[str, Any]) -> set[str]:
    """Collect explicit data-source UIDs used by one dashboard panel."""
    uids: set[str] = set()
    panel_datasource = panel.get("datasource", {})
    if isinstance(panel_datasource, dict):
        uid = panel_datasource.get("uid")
        if isinstance(uid, str):
            uids.add(uid)
    for target in panel.get("targets", []):
        target_datasource = target.get("datasource", {})
        if isinstance(target_datasource, dict):
            uid = target_datasource.get("uid")
            if isinstance(uid, str):
                uids.add(uid)
    return uids


def verify_dashboard() -> None:
    """Require the read-only focused dashboard and provisioned data sources."""
    response = get_json(f"{GRAFANA_URL}/api/dashboards/uid/{DASHBOARD_UID}")
    dashboard = response.get("dashboard", {})
    if dashboard.get("uid") != DASHBOARD_UID or dashboard.get("editable") is not False:
        raise AssertionError("provisioned dashboard UID/read-only state is incorrect")

    panels = dashboard.get("panels", [])
    titles = {panel.get("title") for panel in panels}
    if titles != EXPECTED_PANELS:
        raise AssertionError(
            f"dashboard panels differ: missing={EXPECTED_PANELS - titles}, "
            f"extra={titles - EXPECTED_PANELS}"
        )
    referenced_uids = set().union(*(panel_datasource_uids(panel) for panel in panels))
    if not referenced_uids <= EXPECTED_DATASOURCES:
        raise AssertionError(
            f"dashboard uses unexpected data sources: {referenced_uids}"
        )


def proxy_url(uid: str, path: str, parameters: dict[str, Any] | None = None) -> str:
    """Build a URL through Grafana's server-side data-source proxy."""
    url = f"{GRAFANA_URL}/api/datasources/proxy/uid/{uid}{path}"
    if parameters:
        url = f"{url}?{urllib.parse.urlencode(parameters)}"
    return url


def trace_available(trace_id: str) -> bool:
    """Return whether the known trace is queryable through Grafana and Tempo."""
    try:
        payload = get_json(proxy_url("tempo", f"/api/v2/traces/{trace_id}"))
    except RuntimeError as error:
        if "HTTP 404" in str(error):
            return False
        raise
    trace = payload.get("trace", payload)
    resource_spans = trace.get("resourceSpans", [])
    return isinstance(resource_spans, list) and bool(resource_spans)


def correlated_log_available(
    request_id: str,
    trace_id: str,
    started_at: float,
) -> bool:
    """Return whether the exact log is queryable through Grafana and Loki."""
    expression = (
        '{service="observability-demo-api",environment="local"}'
        f' | json | request_id = "{request_id}" | trace_id = "{trace_id}"'
    )
    payload = get_json(
        proxy_url(
            "loki",
            "/loki/api/v1/query_range",
            {
                "query": expression,
                "start": int((started_at - 5) * 1_000_000_000),
                "end": time.time_ns() + 5_000_000_000,
                "direction": "backward",
                "limit": 100,
            },
        )
    )
    return bool(payload.get("data", {}).get("result", []))


def exemplar_available(trace_id: str, started_at: float) -> bool:
    """Return whether Prometheus stored the known trace on a duration exemplar."""
    expression = (
        "demo_http_server_request_duration_seconds_bucket"
        '{service_name="observability-demo-api",http_route="/slow"}'
    )
    payload = get_json(
        proxy_url(
            "prometheus",
            "/api/v1/query_exemplars",
            {
                "query": expression,
                "start": started_at - 5,
                "end": time.time() + 5,
            },
        )
    )
    for series in payload.get("data", []):
        for exemplar in series.get("exemplars", []):
            if exemplar.get("labels", {}).get("trace_id") == trace_id:
                return True
    return False


def duration_buckets_available(started_at: float) -> bool:
    """Require sub-second application buckets used by the latency panels."""
    selector = (
        "demo_http_server_request_duration_seconds_bucket"
        '{service_name="observability-demo-api",http_route="/slow"}'
    )
    payload = get_json(
        proxy_url(
            "prometheus",
            "/api/v1/series",
            {
                "match[]": selector,
                "start": started_at - 5,
                "end": time.time() + 5,
            },
        )
    )
    observed_boundaries = {
        str(series["le"])
        for series in payload.get("data", [])
        if isinstance(series, dict) and "le" in series
    }
    return {"0.05", "0.5", "1"} <= observed_boundaries


def main() -> None:
    health = get_json(f"{GRAFANA_URL}/api/health")
    if health.get("database") != "ok":
        raise SystemExit(f"Grafana database is not healthy: {health}")
    verify_datasources()
    verify_dashboard()

    slow_request_id = str(uuid4())
    slow_trace_id = uuid4().hex
    error_request_id = str(uuid4())
    error_trace_id = uuid4().hex
    started_at = time.time()
    send_request(
        "/slow?delay_seconds=0.35",
        request_id=slow_request_id,
        trace_id=slow_trace_id,
        expected_status=200,
    )
    send_request(
        "/error",
        request_id=error_request_id,
        trace_id=error_trace_id,
        expected_status=500,
    )

    deadline = time.monotonic() + DEADLINE_SECONDS
    last_error: Exception | None = None
    checks = {
        "trace": False,
        "log": False,
        "exemplar": False,
        "duration_buckets": False,
    }
    while time.monotonic() < deadline:
        try:
            checks["trace"] = trace_available(slow_trace_id)
            checks["log"] = correlated_log_available(
                slow_request_id,
                slow_trace_id,
                started_at,
            )
            checks["exemplar"] = exemplar_available(slow_trace_id, started_at)
            checks["duration_buckets"] = duration_buckets_available(started_at)
            if all(checks.values()):
                print(
                    json.dumps(
                        {
                            "dashboard_uid": DASHBOARD_UID,
                            "datasource_uids": sorted(EXPECTED_DATASOURCES),
                            "error_request_id": error_request_id,
                            "error_trace_id": error_trace_id,
                            "slow_request_id": slow_request_id,
                            "slow_trace_id": slow_trace_id,
                        }
                    )
                )
                return
            last_error = None
        except (OSError, KeyError, TypeError, ValueError, AssertionError) as error:
            last_error = error
        time.sleep(1)

    message = f"Grafana correlation acceptance failed: {checks}"
    if last_error is not None:
        message = f"{message}; last error: {last_error}"
    raise SystemExit(message)


if __name__ == "__main__":
    main()

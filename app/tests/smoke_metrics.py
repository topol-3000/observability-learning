"""Verify application, edge, and synthetic metrics reach Prometheus."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from observability_demo.settings import MetricsSmokeSettings

settings = MetricsSmokeSettings()
API_URL = settings.api_url
PROMETHEUS_URL = settings.prometheus_url
DEADLINE_SECONDS = settings.deadline_seconds
APPLICATION_SERVICE = "observability-demo-api"
UNSAFE_LABELS = {
    "http_url",
    "http_target",
    "url_full",
    "url_path",
    "url_query",
    "request_id",
    "trace_id",
    "span_id",
    "service_instance_id",
    "process_pid",
}


def request(path: str, *, expect_error: bool = False) -> None:
    """Make one request through Traefik and require the expected HTTP result."""
    try:
        with urllib.request.urlopen(f"{API_URL}{path}", timeout=5) as response:  # noqa: S310
            if response.status != 200:
                raise RuntimeError(f"{path} returned HTTP {response.status}")
    except urllib.error.HTTPError as error:
        if not expect_error or error.code != 500:
            raise


def query(expression: str) -> list[dict[str, Any]]:
    """Return vector results from Prometheus's internal HTTP API."""
    encoded_expression = urllib.parse.urlencode({"query": expression})
    request_url = f"{PROMETHEUS_URL}/api/v1/query?{encoded_expression}"
    with urllib.request.urlopen(request_url, timeout=5) as response:  # noqa: S310
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")
    data = payload.get("data", {})
    if data.get("resultType") != "vector":
        raise RuntimeError(f"expected vector result, received {data.get('resultType')}")
    result = data.get("result", [])
    return result if isinstance(result, list) else []


def scalar_at_least(expression: str, minimum: float) -> bool:
    """Return whether a vector query has one finite value at least minimum."""
    result = query(expression)
    if len(result) != 1:
        return False
    value = result[0].get("value", [None, "nan"])
    try:
        return float(value[1]) >= minimum
    except IndexError, TypeError, ValueError:
        return False


def application_labels_are_safe() -> bool:
    """Ensure Prometheus application series expose no unsafe cardinality labels."""
    results = query(
        'demo_http_server_request_count_total{service_name="observability-demo-api"}'
    )
    if not results:
        return False
    expected = {
        "service_name",
        "service_namespace",
        "deployment_environment_name",
        "http_request_method",
        "http_route",
        "http_response_status_code",
        "event_outcome",
    }
    for result in results:
        labels = result.get("metric", {})
        if not expected <= labels.keys() or UNSAFE_LABELS & labels.keys():
            return False
        if labels["http_route"] not in {"/", "/work", "/slow", "/error"}:
            return False
    return True


def metrics_available() -> bool:
    """Check Step 4 acceptance signals after the export/scrape delay."""
    app_selector = f'service_name="{APPLICATION_SERVICE}"'
    return (
        scalar_at_least(
            f"sum(demo_http_server_request_count_total{{{app_selector}}})",
            4,
        )
        and scalar_at_least(
            f"sum(demo_http_server_request_duration_seconds_count{{{app_selector}}})",
            4,
        )
        and scalar_at_least(f"sum(demo_work_count_total{{{app_selector}}})", 1)
        and scalar_at_least('count({__name__=~"traefik_.*"})', 1)
        and scalar_at_least('probe_success{job="api_blackbox"}', 1)
        and scalar_at_least(
            f"sum(demo:api_http_server_request:rate5m{{{app_selector}}})",
            0,
        )
        and application_labels_are_safe()
    )


def main() -> None:
    request("/")
    request("/work?units=2")
    request("/slow?delay_seconds=0.05")
    request("/error", expect_error=True)

    deadline = time.monotonic() + DEADLINE_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if metrics_available():
                print(
                    json.dumps(
                        {
                            "application_service": APPLICATION_SERVICE,
                            "metrics": [
                                "demo_http_server_request_count_total",
                                "demo_http_server_request_duration_seconds",
                                "demo_work_count_total",
                                "traefik_*",
                                "probe_success",
                            ],
                        }
                    )
                )
                return
        except (OSError, ValueError, RuntimeError) as error:
            last_error = error
        time.sleep(1)
    message = "Prometheus did not receive all Step 4 metrics before the deadline"
    if last_error is not None:
        message = f"{message}; last error: {last_error}"
    raise SystemExit(message)


if __name__ == "__main__":
    main()

"""Verify Docker logs reach Loki with safe labels and searchable correlation."""

import json
import time
import urllib.parse
import urllib.request
from typing import Any
from uuid import uuid4

from observability_demo.settings import LogsSmokeSettings

settings = LogsSmokeSettings()
API_URL = settings.api_url
LOKI_URL = settings.loki_url
EXPECTED_REPLICAS = settings.expected_replicas
DEADLINE_SECONDS = settings.deadline_seconds
EXPECTED_STREAM_LABELS = {"service", "environment", "compose_service"}
EXPECTED_SERVICES = {
    "observability-demo-api": "api",
    "observability-demo-edge": "traefik",
}


def send_correlated_request(request_id: str, trace_id: str) -> None:
    """Send a traced request through Traefik and verify request-ID propagation."""
    parent_id = uuid4().hex[:16]
    request = urllib.request.Request(
        f"{API_URL}/work?units=2",
        headers={
            "Connection": "close",
            "Traceparent": f"00-{trace_id}-{parent_id}-01",
            "X-Request-ID": request_id,
        },
    )
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"log request returned HTTP {response.status}")
        if response.headers.get("X-Request-ID") != request_id:
            raise RuntimeError("application did not return the supplied request ID")


def query_loki(expression: str, *, limit: int = 5000) -> list[dict[str, Any]]:
    """Run a recent Loki range query and return stream results."""
    now_ns = time.time_ns()
    parameters = urllib.parse.urlencode(
        {
            "query": expression,
            "start": now_ns - 300_000_000_000,
            "end": now_ns + 5_000_000_000,
            "direction": "backward",
            "limit": limit,
        }
    )
    with urllib.request.urlopen(  # noqa: S310
        f"{LOKI_URL}/loki/api/v1/query_range?{parameters}",
        timeout=5,
    ) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError(f"Loki query failed: {payload}")
    data = payload.get("data", {})
    if data.get("resultType") != "streams":
        raise RuntimeError(
            f"expected stream results, received {data.get('resultType')}"
        )
    result = data.get("result", [])
    return result if isinstance(result, list) else []


def series_loki(selector: str) -> list[dict[str, str]]:
    """Return indexed Loki series for a recent selector."""
    now_ns = time.time_ns()
    parameters = urllib.parse.urlencode(
        {
            "match[]": selector,
            "start": now_ns - 300_000_000_000,
            "end": now_ns,
        }
    )
    with urllib.request.urlopen(  # noqa: S310
        f"{LOKI_URL}/loki/api/v1/series?{parameters}",
        timeout=5,
    ) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError(f"Loki series query failed: {payload}")
    result = payload.get("data", [])
    return result if isinstance(result, list) else []


def lines(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse JSON objects from Loki stream values, retaining malformed lines."""
    records: list[dict[str, Any]] = []
    for result in results:
        for value in result.get("values", []):
            try:
                parsed = json.loads(value[1])
            except IndexError, TypeError, json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def stream_labels_are_safe(series: list[dict[str, str]]) -> bool:
    """Require the exact allowlisted stream labels and valid fixed mappings."""
    observed_services: set[str] = set()
    for labels in series:
        if set(labels) != EXPECTED_STREAM_LABELS:
            unsafe_labels = sorted(set(labels) - EXPECTED_STREAM_LABELS)
            raise AssertionError(f"unsafe Loki stream labels: {unsafe_labels}")
        service = labels.get("service")
        if labels.get("environment") != "local" or EXPECTED_SERVICES.get(
            service
        ) != labels.get("compose_service"):
            raise AssertionError(f"unexpected Loki label values: {labels}")
        observed_services.add(str(service))
    return observed_services == EXPECTED_SERVICES.keys()


def acceptance_satisfied(request_id: str, trace_id: str) -> tuple[bool, set[str]]:
    """Check service coverage, correlation, replica coverage, and label safety."""
    selector = '{environment="local",service=~"observability-demo-(api|edge)"}'
    raw_results = query_loki(selector)
    indexed_series = series_loki(selector)
    if (
        not raw_results
        or not indexed_series
        or not stream_labels_are_safe(indexed_series)
    ):
        return False, set()

    application_records = lines(
        query_loki(
            '{service="observability-demo-api",environment="local"}'
            ' | json | event = "http_request_completed"'
        )
    )
    replica_ids = {
        str(record["service.instance.id"])
        for record in application_records
        if record.get("service.instance.id")
    }

    correlation_results = query_loki(
        '{service="observability-demo-api",environment="local"}'
        f' | json | request_id = "{request_id}" | trace_id = "{trace_id}"'
    )
    if not correlation_results:
        return False, replica_ids

    application_health_records = [
        record
        for record in application_records
        if record.get("http.route") in {"/health/live", "/health/ready"}
    ]
    return (
        len(replica_ids) >= EXPECTED_REPLICAS and not application_health_records,
        replica_ids,
    )


def main() -> None:
    request_id = str(uuid4())
    trace_id = uuid4().hex
    send_correlated_request(request_id, trace_id)
    for _ in range(EXPECTED_REPLICAS * 6):
        send_correlated_request(str(uuid4()), uuid4().hex)

    deadline = time.monotonic() + DEADLINE_SECONDS
    last_error: Exception | None = None
    observed_replicas: set[str] = set()
    while time.monotonic() < deadline:
        try:
            accepted, observed_replicas = acceptance_satisfied(request_id, trace_id)
            if accepted:
                print(
                    json.dumps(
                        {
                            "labels": sorted(EXPECTED_STREAM_LABELS),
                            "observed_application_instances": sorted(observed_replicas),
                            "request_id": request_id,
                            "trace_id": trace_id,
                        }
                    )
                )
                return
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            AssertionError,
        ) as error:
            last_error = error
        time.sleep(1)

    message = (
        f"Loki acceptance failed; expected {EXPECTED_REPLICAS} replicas, "
        f"observed {len(observed_replicas)}"
    )
    if last_error is not None:
        message = f"{message}; last error: {last_error}"
    raise SystemExit(message)


if __name__ == "__main__":
    main()

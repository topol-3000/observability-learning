"""Verify edge/application trace continuity and all replica resource identities."""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from observability_demo.settings import TraceSmokeSettings

settings = TraceSmokeSettings()
API_URL = settings.api_url
TEMPO_URL = settings.tempo_url
EXPECTED_REPLICAS = settings.expected_replicas
DEADLINE_SECONDS = settings.deadline_seconds
EXPECTED_SERVICES = {"observability-demo-edge", "observability-demo-api"}
EXPECTED_WORK_SPANS = {
    "demo.work.validate",
    "demo.work.calculate",
    "demo.work.persist",
}
UNSAFE_SPAN_ATTRIBUTES = {
    "http.request.header.authorization",
    "http.request.header.cookie",
    "http.request.body",
    "http.response.body",
    "http.url",
    "http.target",
    "url.full",
    "url.path",
    "url.query",
}


def otel_attributes(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Flatten OTLP JSON key/value attributes used by the acceptance assertions."""
    attributes: dict[str, Any] = {}
    for item in items:
        value = item.get("value", {})
        for value_key in (
            "stringValue",
            "intValue",
            "doubleValue",
            "boolValue",
        ):
            if value_key in value:
                attributes[str(item["key"])] = value[value_key]
                break
    return attributes


def resource_spans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    trace = payload.get("trace", payload)
    spans = trace.get("resourceSpans", [])
    return spans if isinstance(spans, list) else []


def all_spans(resource_span: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for scope_span in resource_span.get("scopeSpans", []):
        yield from scope_span.get("spans", [])


def generate_trace() -> str:
    trace_id = uuid4().hex
    parent_id = uuid4().hex[:16]
    request = urllib.request.Request(
        API_URL,
        headers={
            "Connection": "close",
            "Traceparent": f"00-{trace_id}-{parent_id}-01",
        },
    )
    with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"trace request returned HTTP {response.status}")
    return trace_id


def fetch_trace(trace_id: str) -> dict[str, Any] | None:
    request = urllib.request.Request(f"{TEMPO_URL}/api/v2/traces/{trace_id}")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def wait_for_trace(trace_id: str, deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        payload = fetch_trace(trace_id)
        if payload is not None:
            services = {
                otel_attributes(
                    resource_span.get("resource", {}).get("attributes", [])
                ).get("service.name")
                for resource_span in resource_spans(payload)
            }
            if EXPECTED_SERVICES <= services:
                return payload
        time.sleep(0.25)
    return None


def verify_trace(payload: dict[str, Any]) -> str:
    services: set[str] = set()
    work_span_names: set[str] = set()
    application_instance = ""
    edge_instance = ""

    for resource_span in resource_spans(payload):
        attributes = otel_attributes(
            resource_span.get("resource", {}).get("attributes", [])
        )
        service_name = str(attributes.get("service.name", ""))
        services.add(service_name)
        if service_name in EXPECTED_SERVICES:
            if attributes.get("service.namespace") != "learning":
                raise AssertionError(f"{service_name} has inconsistent namespace")
            if attributes.get("deployment.environment.name") != "local":
                raise AssertionError(f"{service_name} has inconsistent environment")
        for span in all_spans(resource_span):
            unsafe = (
                UNSAFE_SPAN_ATTRIBUTES
                & otel_attributes(span.get("attributes", [])).keys()
            )
            if unsafe:
                raise AssertionError(
                    f"unsafe span attributes survived collection: {sorted(unsafe)}"
                )
        if service_name == "observability-demo-api":
            application_instance = str(attributes.get("service.instance.id", ""))
            work_span_names.update(
                str(span.get("name", "")) for span in all_spans(resource_span)
            )
        elif service_name == "observability-demo-edge":
            edge_instance = str(attributes.get("service.instance.id", ""))

    if not EXPECTED_SERVICES <= services:
        raise AssertionError(f"trace services were {sorted(services)}")
    if not EXPECTED_WORK_SPANS <= work_span_names:
        raise AssertionError(f"work spans were {sorted(work_span_names)}")
    if not application_instance:
        raise AssertionError("application service.instance.id is missing")
    if not edge_instance:
        raise AssertionError("edge service.instance.id is missing")
    return application_instance


def main() -> None:
    deadline = time.monotonic() + DEADLINE_SECONDS
    instances: set[str] = set()
    checked_traces: list[str] = []
    last_error: Exception | None = None

    while time.monotonic() < deadline and len(instances) < EXPECTED_REPLICAS:
        try:
            trace_id = generate_trace()
            payload = wait_for_trace(trace_id, deadline)
            if payload is None:
                raise TimeoutError(f"Tempo did not return complete trace {trace_id}")
            instances.add(verify_trace(payload))
            checked_traces.append(trace_id)
            last_error = None
        except (OSError, KeyError, ValueError, AssertionError) as error:
            last_error = error
            time.sleep(0.25)

    print(
        json.dumps(
            {
                "observed_application_instances": sorted(instances),
                "verified_trace_ids": checked_traces,
            }
        )
    )
    if len(instances) != EXPECTED_REPLICAS:
        message = f"expected {EXPECTED_REPLICAS} replicas, observed {len(instances)}"
        if last_error is not None:
            message = f"{message}; last error: {last_error}"
        raise SystemExit(message)


if __name__ == "__main__":
    main()

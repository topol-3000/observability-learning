"""Unit tests for the baseline FastAPI application."""

import asyncio
import io
import json
import logging
import os
import socket
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from uuid import UUID

import pytest
from fastapi import Request
from httpx2 import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode

from observability_demo.logging import JsonFormatter, trace_log_context
from observability_demo.main import (
    MAX_DELAY_SECONDS,
    MAX_WORK_UNITS,
    REQUEST_ID_HEADER,
    create_app,
)
from observability_demo.tracing import create_trace_runtime, service_resource

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    application = create_app(create_trace_runtime(enabled=False))
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as test_client:
            yield test_client


@pytest.fixture
async def traced_client() -> AsyncIterator[tuple[AsyncClient, InMemorySpanExporter]]:
    exporter = InMemorySpanExporter()
    runtime = create_trace_runtime(exporter=exporter, enabled=True, batch=False)
    application = create_app(runtime)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as test_client:
            yield test_client, exporter


@pytest.fixture
def log_output() -> Iterator[io.StringIO]:
    """Capture only application JSON logs without changing their formatter."""
    application_logger = logging.getLogger("observability_demo")
    previous_handlers = application_logger.handlers[:]
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    application_logger.handlers = [handler]
    try:
        yield stream
    finally:
        application_logger.handlers = previous_handlers


def parsed_logs(log_output: io.StringIO) -> list[dict[str, object]]:
    """Parse captured output and prove every physical line is valid JSON."""
    return [json.loads(line) for line in log_output.getvalue().splitlines()]


def completion_logs(log_output: io.StringIO) -> list[dict[str, object]]:
    return [
        record
        for record in parsed_logs(log_output)
        if record["event"] == "http_request_completed"
    ]


async def test_root(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.json()["message"] == "observability demo API"


async def test_work_is_bounded_and_returns_result(client: AsyncClient) -> None:
    response = await client.get("/work", params={"units": 2})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["units"] == 2
    assert isinstance(response.json()["checksum"], int)

    rejected = await client.get("/work", params={"units": MAX_WORK_UNITS + 1})
    assert rejected.status_code == 422


async def test_slow_delay_is_bounded(client: AsyncClient) -> None:
    response = await client.get("/slow", params={"delay_seconds": 0.001})

    assert response.status_code == 200
    assert response.json() == {"status": "completed", "delay_seconds": 0.001}

    rejected = await client.get(
        "/slow", params={"delay_seconds": MAX_DELAY_SECONDS + 0.001}
    )
    assert rejected.status_code == 422


async def test_error_response_is_generic(client: AsyncClient) -> None:
    response = await client.get("/error")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert "intentional" not in response.text.lower()


async def test_request_emits_one_bounded_json_completion_record(
    client: AsyncClient,
    log_output: io.StringIO,
) -> None:
    request_id = "47f70a2d-2512-44ee-8f2c-0f84f5631e98"

    response = await client.get(
        "/work",
        params={"units": 2},
        headers={REQUEST_ID_HEADER: request_id},
    )

    assert response.headers[REQUEST_ID_HEADER] == request_id
    records = completion_logs(log_output)
    assert len(records) == 1
    record = records[0]
    assert record["severity"] == "INFO"
    assert record["logger"] == "observability_demo.main"
    assert record["service.name"] == "observability-demo-api"
    assert record["service.namespace"] == "learning"
    assert record["service.version"] == "0.1.0"
    assert record["deployment.environment.name"] == "local"
    UUID(str(record["service.instance.id"]))
    assert record["container.id"] == socket.gethostname()
    assert record["process.pid"] == os.getpid()
    assert datetime.fromisoformat(str(record["timestamp"]).replace("Z", "+00:00"))
    assert record["request_id"] == request_id
    assert record["http.request.method"] == "GET"
    assert record["http.route"] == "/work"
    assert record["http.response.status_code"] == 200
    assert record["event.outcome"] == "success"
    assert isinstance(record["duration_ms"], int | float)
    assert "trace_id" not in record
    assert "span_id" not in record
    assert "units" not in record


async def test_missing_or_invalid_request_id_is_safely_replaced(
    client: AsyncClient,
    log_output: io.StringIO,
) -> None:
    invalid_request_id = "secret-shaped-but-not-a-uuid"

    response = await client.get("/", headers={REQUEST_ID_HEADER: invalid_request_id})

    generated_request_id = response.headers[REQUEST_ID_HEADER]
    UUID(generated_request_id)
    assert generated_request_id != invalid_request_id
    assert completion_logs(log_output)[0]["request_id"] == generated_request_id
    assert invalid_request_id not in log_output.getvalue()


async def test_concurrent_requests_keep_request_context_isolated(
    client: AsyncClient,
    log_output: io.StringIO,
) -> None:
    request_ids = {
        "2c10c270-7b2c-44ec-9645-6f5809c2a332",
        "65e92209-97b4-4717-8117-ec7a42534698",
    }

    responses = await asyncio.gather(
        *(
            client.get(
                "/slow",
                params={"delay_seconds": 0.001},
                headers={REQUEST_ID_HEADER: request_id},
            )
            for request_id in request_ids
        )
    )

    assert {
        response.headers[REQUEST_ID_HEADER] for response in responses
    } == request_ids
    records = completion_logs(log_output)
    assert len(records) == 2
    assert {str(record["request_id"]) for record in records} == request_ids


async def test_health_requests_are_excluded_from_application_request_logs(
    client: AsyncClient,
    log_output: io.StringIO,
) -> None:
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/health/ready")).status_code == 200

    assert completion_logs(log_output) == []


async def test_error_completion_has_exception_context_without_message(
    client: AsyncClient,
    log_output: io.StringIO,
) -> None:
    response = await client.get("/error")

    assert response.status_code == 500
    records = completion_logs(log_output)
    assert len(records) == 1
    record = records[0]
    assert record["severity"] == "ERROR"
    assert record["event.outcome"] == "server_error"
    assert record["exception.type"] == "IntentionalDemoError"
    assert str(record["exception.stacktrace"]).startswith(
        "Traceback (most recent call last):"
    )
    assert "exception.message" not in record


async def test_request_secrets_and_exception_message_are_never_logged(
    log_output: io.StringIO,
) -> None:
    application = create_app(create_trace_runtime(enabled=False))

    @application.post("/unexpected")
    async def unexpected(request: Request) -> None:
        raise RuntimeError(request.headers["x-demo-secret"])

    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as test_client:
            response = await test_client.post(
                "/unexpected",
                params={"api_key": "query-secret-4381"},
                headers={
                    "Authorization": "Bearer authorization-secret-7192",
                    "Cookie": "session=cookie-secret-6204",
                    "X-Demo-Secret": "exception-secret-9053",
                },
                content="body-secret-1846",
            )

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    output = log_output.getvalue()
    for secret in (
        "query-secret-4381",
        "authorization-secret-7192",
        "cookie-secret-6204",
        "exception-secret-9053",
        "body-secret-1846",
    ):
        assert secret not in output
    record = completion_logs(log_output)[0]
    assert record["exception.type"] == "RuntimeError"
    assert record["http.route"] == "/unexpected"


def test_trace_placeholders_are_bound_only_inside_trace_context(
    log_output: io.StringIO,
) -> None:
    trace_logger = logging.getLogger("observability_demo.trace_test")

    with trace_log_context("A" * 32, "B" * 16):
        trace_logger.info("inside_trace")
    trace_logger.info("outside_trace")

    inside, outside = parsed_logs(log_output)
    assert inside["trace_id"] == "a" * 32
    assert inside["span_id"] == "b" * 16
    assert "trace_id" not in outside
    assert "span_id" not in outside


async def test_instance_reports_current_replica_and_process(
    client: AsyncClient,
) -> None:
    response = await client.get("/debug/instance")

    assert response.status_code == 200
    assert response.json() == {"instance_id": socket.gethostname(), "pid": os.getpid()}


async def test_liveness_does_not_depend_on_readiness(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


async def test_readiness_tracks_application_lifespan() -> None:
    application = create_app(create_trace_runtime(enabled=False))

    assert application.state.ready is False
    async with application.router.lifespan_context(application):
        assert application.state.ready is True
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as test_client:
            response = await test_client.get("/health/ready")
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}

    assert application.state.ready is False


async def test_work_trace_has_remote_parent_and_bounded_manual_children(
    traced_client: tuple[AsyncClient, InMemorySpanExporter],
    log_output: io.StringIO,
) -> None:
    client, exporter = traced_client
    trace_id = "1234567890abcdef1234567890abcdef"
    remote_parent_id = "1234567890abcdef"

    response = await client.get(
        "/work",
        params={"units": 2},
        headers={"traceparent": f"00-{trace_id}-{remote_parent_id}-01"},
    )

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    server_span = next(span for span in spans if span.kind is SpanKind.SERVER)
    assert f"{server_span.context.trace_id:032x}" == trace_id
    assert server_span.parent is not None
    assert f"{server_span.parent.span_id:016x}" == remote_parent_id
    assert server_span.resource.attributes["service.name"] == ("observability-demo-api")
    assert server_span.resource.attributes["service.namespace"] == "learning"
    UUID(str(server_span.resource.attributes["service.instance.id"]))

    manual_spans = {
        span.name: span for span in spans if span.name.startswith("demo.work.")
    }
    assert set(manual_spans) == {
        "demo.work.validate",
        "demo.work.calculate",
        "demo.work.persist",
    }
    assert all(
        span.parent is not None
        and span.parent.span_id == server_span.context.span_id
        and span.context.trace_id == server_span.context.trace_id
        for span in manual_spans.values()
    )
    assert manual_spans["demo.work.validate"].attributes == {
        "demo.work.units": 2,
        "demo.work.outcome": "success",
    }
    for name in ("demo.work.calculate", "demo.work.persist"):
        assert manual_spans[name].attributes == {"demo.work.outcome": "success"}

    completion = completion_logs(log_output)[0]
    assert completion["trace_id"] == trace_id
    assert completion["span_id"] == f"{server_span.context.span_id:016x}"


async def test_health_requests_do_not_create_spans(
    traced_client: tuple[AsyncClient, InMemorySpanExporter],
) -> None:
    client, exporter = traced_client

    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/health/ready")).status_code == 200

    assert exporter.get_finished_spans() == ()


async def test_error_span_is_failed_without_exception_message(
    traced_client: tuple[AsyncClient, InMemorySpanExporter],
) -> None:
    client, exporter = traced_client

    response = await client.get("/error")

    assert response.status_code == 500
    server_span = next(
        span for span in exporter.get_finished_spans() if span.kind is SpanKind.SERVER
    )
    assert server_span.status.status_code is StatusCode.ERROR
    exception_event = next(
        event for event in server_span.events if event.name == "exception"
    )
    assert exception_event.attributes["exception.type"] == (
        "observability_demo.main.IntentionalDemoError"
    )
    assert str(exception_event.attributes["exception.stacktrace"]).startswith(
        "Traceback (most recent call last):"
    )
    assert all(
        "exception.message" not in event.attributes for event in server_span.events
    )


def test_trace_resource_matches_structured_log_identity() -> None:
    attributes = service_resource().attributes

    assert attributes["service.name"] == "observability-demo-api"
    assert attributes["service.namespace"] == "learning"
    assert attributes["service.version"] == "0.1.0"
    assert attributes["deployment.environment.name"] == "local"
    UUID(str(attributes["service.instance.id"]))

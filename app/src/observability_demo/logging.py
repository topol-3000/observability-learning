"""Structured logging and correlation context for the demo service."""

import json
import logging
import os
import re
import socket
import sys
import traceback
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import UUID, uuid4

SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "observability-demo-api")
SERVICE_NAMESPACE = os.getenv("OTEL_SERVICE_NAMESPACE", "learning")
SERVICE_VERSION = os.getenv("APP_VERSION", "0.1.0")
DEPLOYMENT_ENVIRONMENT = os.getenv("DEPLOYMENT_ENVIRONMENT", "local")
SERVICE_INSTANCE_ID = os.environ.setdefault("SERVICE_INSTANCE_ID", str(uuid4()))
CONTAINER_ID = socket.gethostname()

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_span_id: ContextVar[str | None] = ContextVar("span_id", default=None)

_TRACE_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_SPAN_ID_PATTERN = re.compile(r"[0-9a-f]{16}")
_EVENT_FIELDS = frozenset(
    {
        "duration_ms",
        "event.outcome",
        "http.request.method",
        "http.response.status_code",
        "http.route",
    }
)


class JsonFormatter(logging.Formatter):
    """Format reviewed log data as one compact JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, bool | float | int | str] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "severity": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "service.name": SERVICE_NAME,
            "service.namespace": SERVICE_NAMESPACE,
            "service.version": SERVICE_VERSION,
            "deployment.environment.name": DEPLOYMENT_ENVIRONMENT,
            "service.instance.id": SERVICE_INSTANCE_ID,
            "container.id": CONTAINER_ID,
            "process.pid": record.process,
        }

        request_id = _request_id.get()
        trace_id = _trace_id.get()
        span_id = _span_id.get()
        if request_id is not None:
            payload["request_id"] = request_id
        if trace_id is not None:
            payload["trace_id"] = trace_id
        if span_id is not None:
            payload["span_id"] = span_id

        event_fields = getattr(record, "event_fields", None)
        if isinstance(event_fields, Mapping):
            for key in _EVENT_FIELDS:
                value = event_fields.get(key)
                if isinstance(value, bool | float | int | str):
                    payload[key] = value

        if record.exc_info is not None:
            exception_type, _exception, exception_traceback = record.exc_info
            if exception_type is not None:
                payload["exception.type"] = exception_type.__name__
            if exception_traceback is not None:
                payload["exception.stacktrace"] = (
                    "Traceback (most recent call last):\n"
                    + "".join(traceback.format_tb(exception_traceback))
                ).rstrip()

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_application_logging() -> None:
    """Install one stdout JSON handler for application loggers."""
    application_logger = logging.getLogger("observability_demo")
    if any(
        getattr(handler, "_observability_demo_json", False)
        for handler in application_logger.handlers
    ):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._observability_demo_json = True  # type: ignore[attr-defined]
    application_logger.addHandler(handler)
    application_logger.setLevel(logging.INFO)
    application_logger.propagate = False


@contextmanager
def request_log_context(request_id: str) -> Iterator[None]:
    """Bind a request ID to logs in the current asynchronous context."""
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


@contextmanager
def trace_log_context(trace_id: str, span_id: str) -> Iterator[None]:
    """Bind validated trace correlation values for future instrumentation."""
    normalized_trace_id = trace_id.lower()
    normalized_span_id = span_id.lower()
    if _TRACE_ID_PATTERN.fullmatch(normalized_trace_id) is None:
        raise ValueError("trace_id must contain exactly 32 hexadecimal characters")
    if _SPAN_ID_PATTERN.fullmatch(normalized_span_id) is None:
        raise ValueError("span_id must contain exactly 16 hexadecimal characters")

    trace_token = _trace_id.set(normalized_trace_id)
    span_token = _span_id.set(normalized_span_id)
    try:
        yield
    finally:
        _span_id.reset(span_token)
        _trace_id.reset(trace_token)


def new_request_id() -> str:
    """Return a non-semantic, globally unique request correlation ID."""
    return str(uuid4())


def valid_request_id(value: str | None) -> bool:
    """Accept only canonical UUID-shaped IDs to bound client-provided content."""
    if value is None or len(value) != 36:
        return False
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False

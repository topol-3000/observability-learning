"""Application middleware for bounded request telemetry and logging."""

import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from observability_demo.logging import (
    new_request_id,
    request_log_context,
    trace_log_context,
    valid_request_id,
)
from observability_demo.tracing import current_trace_ids, mark_current_span_failed

REQUEST_ID_HEADER = "X-Request-ID"
HEALTH_PATHS = frozenset({"/health/live", "/health/ready"})
KNOWN_HTTP_METHODS = frozenset(
    {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
)

logger = logging.getLogger(__name__)


def request_method(method: str) -> str:
    """Return a bounded HTTP method value for telemetry."""
    normalized_method = method.upper()
    return normalized_method if normalized_method in KNOWN_HTTP_METHODS else "OTHER"


def request_route(request: Request) -> str:
    """Return a matched route template without raw path or query data."""
    route: Any = request.scope.get("route")
    route_template = getattr(route, "path", None)
    return route_template if isinstance(route_template, str) else "unmatched"


def request_outcome(status_code: int) -> str:
    """Classify an HTTP response with a small, stable value set."""
    if status_code >= 500:
        return "server_error"
    if status_code >= 400:
        return "client_error"
    return "success"


def install_structured_request_logging(application: FastAPI) -> None:
    """Register request correlation, bounded HTTP metrics, and completion logs."""

    @application.middleware("http")
    async def structured_request_log(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied_request_id = request.headers.get(REQUEST_ID_HEADER)
        request_id = (
            supplied_request_id
            if valid_request_id(supplied_request_id)
            else new_request_id()
        )
        started_at = time.perf_counter()
        method = request_method(request.method)
        metrics_runtime = request.app.state.metrics_runtime
        response: Response | None = None
        request_exception: Exception | None = None

        with request_log_context(request_id):
            if request.url.path not in HEALTH_PATHS:
                metrics_runtime.record_http_started(method)
            try:
                try:
                    response = await call_next(request)
                except Exception as exception:  # noqa: BLE001
                    request_exception = exception
                    response = JSONResponse(
                        status_code=500,
                        content={"detail": "Internal Server Error"},
                    )

                handled_exception = getattr(request.state, "handled_exception", None)
                if isinstance(handled_exception, Exception):
                    request_exception = handled_exception

                response.headers[REQUEST_ID_HEADER] = request_id
                return response
            finally:
                if request.url.path not in HEALTH_PATHS:
                    status_code = response.status_code if response is not None else 500
                    duration_seconds = time.perf_counter() - started_at
                    metrics_runtime.record_http_completed(
                        duration_seconds,
                        {
                            "http.request.method": method,
                            "http.route": request_route(request),
                            "http.response.status_code": status_code,
                            "event.outcome": request_outcome(status_code),
                        },
                    )
                    if status_code >= 500:
                        mark_current_span_failed(request_exception)
                    event_fields = {
                        "duration_ms": round(duration_seconds * 1_000, 3),
                        "event.outcome": request_outcome(status_code),
                        "http.request.method": method,
                        "http.response.status_code": status_code,
                        "http.route": request_route(request),
                    }
                    trace_ids = current_trace_ids()
                    log_context = (
                        nullcontext()
                        if trace_ids is None
                        else trace_log_context(*trace_ids)
                    )
                    with log_context:
                        if request_exception is None:
                            logger.info(
                                "http_request_completed",
                                extra={"event_fields": event_fields},
                            )
                        else:
                            logger.error(
                                "http_request_completed",
                                extra={"event_fields": event_fields},
                                exc_info=(
                                    type(request_exception),
                                    request_exception,
                                    request_exception.__traceback__,
                                ),
                            )

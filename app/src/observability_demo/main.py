"""FastAPI application used by the observability learning stack."""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, nullcontext
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from observability_demo.logging import (
    configure_application_logging,
    new_request_id,
    request_log_context,
    trace_log_context,
    valid_request_id,
)
from observability_demo.metrics import MetricsRuntime, create_metrics_runtime
from observability_demo.routes import (
    IntentionalDemoError,
    router,
)
from observability_demo.settings import ApplicationSettings
from observability_demo.tracing import (
    TraceRuntime,
    create_trace_runtime,
    current_trace_ids,
    instrument_fastapi,
    mark_current_span_failed,
)

REQUEST_ID_HEADER = "X-Request-ID"
APP_VERSION = ApplicationSettings().version
HEALTH_PATHS = frozenset({"/health/live", "/health/ready"})
KNOWN_HTTP_METHODS = frozenset(
    {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
)

configure_application_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Expose readiness only while the application can serve requests."""
    application.state.ready = True
    try:
        yield
    finally:
        application.state.ready = False
        application.state.metrics_runtime.shutdown()
        application.state.trace_runtime.shutdown()


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


def create_app(
    trace_runtime: TraceRuntime | None = None,
    metrics_runtime: MetricsRuntime | None = None,
) -> FastAPI:
    """Build a new application instance for the server and tests."""
    runtime = trace_runtime if trace_runtime is not None else create_trace_runtime()
    metric_runtime = (
        metrics_runtime if metrics_runtime is not None else create_metrics_runtime()
    )
    application = FastAPI(
        title="Observability Demo API",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    application.state.ready = False
    application.state.trace_runtime = runtime
    application.state.metrics_runtime = metric_runtime

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
        response: Response | None = None
        request_exception: Exception | None = None

        with request_log_context(request_id):
            if request.url.path not in HEALTH_PATHS:
                metric_runtime.record_http_started(method)
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
                    metric_runtime.record_http_completed(
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

    @application.exception_handler(IntentionalDemoError)
    async def handle_intentional_error(
        request: Request, exception: IntentionalDemoError
    ) -> JSONResponse:
        request.state.handled_exception = exception
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
        )

    application.include_router(router)
    instrument_fastapi(application, runtime)
    return application


app = create_app()

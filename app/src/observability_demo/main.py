"""FastAPI application used by the observability learning stack."""

import asyncio
import logging
import os
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response

from observability_demo.logging import (
    configure_application_logging,
    new_request_id,
    request_log_context,
    valid_request_id,
)

APP_VERSION = os.getenv("APP_VERSION", "0.1.0")
INSTANCE_ID = socket.gethostname()
MAX_WORK_UNITS = 100
MAX_DELAY_SECONDS = 2.0
REQUEST_ID_HEADER = "X-Request-ID"
HEALTH_PATHS = frozenset({"/health/live", "/health/ready"})
KNOWN_HTTP_METHODS = frozenset(
    {"CONNECT", "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE"}
)

configure_application_logging()
logger = logging.getLogger(__name__)


class IntentionalDemoError(Exception):
    """An expected failure used by the error-observability exercises."""


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Expose readiness only while the application can serve requests."""
    application.state.ready = True
    try:
        yield
    finally:
        application.state.ready = False


def perform_bounded_work(units: int) -> int:
    """Perform deterministic, intentionally bounded CPU work."""
    checksum = 0
    for value in range(units * 1_000):
        checksum = (checksum + value * value) % 1_000_003
    return checksum


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


def create_app() -> FastAPI:
    """Build a new application instance for the server and tests."""
    application = FastAPI(
        title="Observability Demo API",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    application.state.ready = False

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
        response: Response | None = None
        request_exception: Exception | None = None

        with request_log_context(request_id):
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
                    event_fields = {
                        "duration_ms": round(
                            (time.perf_counter() - started_at) * 1_000, 3
                        ),
                        "event.outcome": request_outcome(status_code),
                        "http.request.method": request_method(request.method),
                        "http.response.status_code": status_code,
                        "http.route": request_route(request),
                    }
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

    @application.get("/")
    async def root() -> dict[str, str]:
        return {
            "message": "observability demo API",
            "version": APP_VERSION,
        }

    @application.get("/work")
    async def work(
        units: Annotated[int, Query(ge=1, le=MAX_WORK_UNITS)] = 10,
    ) -> dict[str, int | str]:
        checksum = perform_bounded_work(units)
        return {"status": "completed", "units": units, "checksum": checksum}

    @application.get("/slow")
    async def slow(
        delay_seconds: Annotated[
            float,
            Query(gt=0, le=MAX_DELAY_SECONDS),
        ] = 0.25,
    ) -> dict[str, float | str]:
        await asyncio.sleep(delay_seconds)
        return {"status": "completed", "delay_seconds": delay_seconds}

    @application.get("/error")
    async def error() -> None:
        raise IntentionalDemoError("intentional observability exercise failure")

    @application.get("/debug/instance")
    async def instance() -> dict[str, int | str]:
        return {"instance_id": INSTANCE_ID, "pid": os.getpid()}

    @application.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @application.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        is_ready = bool(getattr(request.app.state, "ready", False))
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={"status": "ready" if is_ready else "not ready"},
        )

    return application


app = create_app()

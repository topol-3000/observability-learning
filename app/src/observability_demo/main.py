"""FastAPI application used by the observability learning stack."""

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

APP_VERSION = os.getenv("APP_VERSION", "0.1.0")
INSTANCE_ID = socket.gethostname()
MAX_WORK_UNITS = 100
MAX_DELAY_SECONDS = 2.0


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


def create_app() -> FastAPI:
    """Build a new application instance for the server and tests."""
    application = FastAPI(
        title="Observability Demo API",
        version=APP_VERSION,
        lifespan=lifespan,
    )
    application.state.ready = False

    @application.exception_handler(IntentionalDemoError)
    async def handle_intentional_error(
        _request: Request, _exception: IntentionalDemoError
    ) -> JSONResponse:
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

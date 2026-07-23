"""HTTP route definitions for the observability demo API."""

import asyncio
import os
import socket
import time
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

INSTANCE_ID = socket.gethostname()
MAX_WORK_UNITS = 100
MAX_DELAY_SECONDS = 2.0

router = APIRouter()


class IntentionalDemoError(Exception):
    """An expected failure used by the error-observability exercises."""


def perform_bounded_work(units: int) -> int:
    """Perform deterministic, intentionally bounded CPU work."""
    checksum = 0
    for value in range(units * 1_000):
        checksum = (checksum + value * value) % 1_000_003
    return checksum


@router.get("/")
async def root(request: Request) -> dict[str, str]:
    return {
        "message": "observability demo API",
        "version": request.app.version,
    }


@router.get("/work")
async def work(
    request: Request,
    units: Annotated[int, Query(ge=1, le=MAX_WORK_UNITS)] = 10,
) -> dict[str, int | str]:
    started_at = time.perf_counter()
    tracer = request.app.state.trace_runtime.tracer
    metrics_runtime = request.app.state.metrics_runtime
    try:
        with tracer.start_as_current_span(
            "demo.work.validate",
            attributes={
                "demo.work.units": units,
                "demo.work.outcome": "success",
            },
        ):
            validated_units = units
        with tracer.start_as_current_span(
            "demo.work.calculate",
            attributes={"demo.work.outcome": "success"},
        ):
            checksum = perform_bounded_work(validated_units)
        with tracer.start_as_current_span(
            "demo.work.persist",
            attributes={"demo.work.outcome": "success"},
        ):
            await asyncio.sleep(0)
    except Exception:
        metrics_runtime.record_work_completed(
            time.perf_counter() - started_at,
            "error",
        )
        raise
    metrics_runtime.record_work_completed(
        time.perf_counter() - started_at,
        "success",
    )
    return {"status": "completed", "units": units, "checksum": checksum}


@router.get("/slow")
async def slow(
    delay_seconds: Annotated[
        float,
        Query(gt=0, le=MAX_DELAY_SECONDS),
    ] = 0.25,
) -> dict[str, float | str]:
    await asyncio.sleep(delay_seconds)
    return {"status": "completed", "delay_seconds": delay_seconds}


@router.get("/error")
async def error() -> None:
    raise IntentionalDemoError("intentional observability exercise failure")


@router.get("/debug/instance")
async def instance() -> dict[str, int | str]:
    return {"instance_id": INSTANCE_ID, "pid": os.getpid()}


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    is_ready = bool(getattr(request.app.state, "ready", False))
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ready" if is_ready else "not ready"},
    )

"""FastAPI application used by the observability learning stack."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from observability_demo.logging import configure_application_logging
from observability_demo.metrics import MetricsRuntime, create_metrics_runtime
from observability_demo.middleware import install_structured_request_logging
from observability_demo.routes import (
    IntentionalDemoError,
    router,
)
from observability_demo.settings import ApplicationSettings
from observability_demo.tracing import (
    TraceRuntime,
    create_trace_runtime,
    instrument_fastapi,
)

APP_VERSION = ApplicationSettings().version

configure_application_logging()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Expose readiness only while the application can serve requests."""
    application.state.ready = True
    try:
        yield
    finally:
        application.state.ready = False
        application.state.metrics_runtime.shutdown()
        application.state.trace_runtime.shutdown()


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

    @application.exception_handler(IntentionalDemoError)
    async def handle_intentional_error(
        request: Request, exception: IntentionalDemoError
    ) -> JSONResponse:
        request.state.handled_exception = exception
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"},
        )

    install_structured_request_logging(application)
    application.include_router(router)
    instrument_fastapi(application, runtime)
    return application


app = create_app()

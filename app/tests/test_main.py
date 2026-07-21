"""Unit tests for the baseline FastAPI application."""

import os
import socket
from collections.abc import AsyncIterator

import pytest
from httpx2 import ASGITransport, AsyncClient

from observability_demo.main import (
    MAX_DELAY_SECONDS,
    MAX_WORK_UNITS,
    create_app,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    application = create_app()
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as test_client:
            yield test_client


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
    application = create_app()

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

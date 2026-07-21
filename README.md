# Docker Observability Learning Stack

Step 1 provides a containerized FastAPI service behind Traefik. Docker Compose
runs four API replicas, each with one Granian worker. The API containers have no
host port; Traefik is the only published HTTP entrypoint.

The implementation plan is recorded in
[`docs/STEP_1_IMPLEMENTATION_PLAN.md`](docs/STEP_1_IMPLEMENTATION_PLAN.md).

## Prerequisites

- Docker Engine or Docker Desktop
- Docker Compose plugin

No host Python installation is required.

The application image is pinned to CPython 3.14.6. Direct Python dependencies
and quality tools are pinned to the latest stable versions selected for this
implementation and resolved transitively by `app/uv.lock`.

## Start the baseline

```bash
cp .env.example .env
docker compose up --build --detach --wait
```

Open <http://127.0.0.1:8080/>. The useful Step 1 endpoints are:

| Endpoint | Purpose |
| --- | --- |
| `GET /` | Normal successful request |
| `GET /work?units=10` | Bounded simulated CPU work (`1..100`) |
| `GET /slow?delay_seconds=0.25` | Bounded latency (`0 < delay <= 2`) |
| `GET /error` | Intentional generic HTTP 500 response |
| `GET /debug/instance` | Replica hostname and worker PID |
| `GET /health/live` | Process liveness |
| `GET /health/ready` | Traffic readiness |

Check the resolved containers and their health:

```bash
docker compose ps
```

## Verify load distribution

The smoke check makes repeated requests through Traefik and fails unless it sees
four distinct replica IDs:

```bash
docker compose --profile test run --build --rm smoke
```

You can also refresh <http://127.0.0.1:8080/debug/instance> to inspect routing
manually.

## Verify replica failure behavior

Choose one API container shown by `docker compose ps api`, stop it, and verify
that Traefik continues routing only to the remaining three:

```bash
docker compose ps api
docker stop <one-api-container-name>
docker compose --profile test run --rm --no-deps \
  --env EXPECTED_REPLICAS=3 smoke
```

Restore the declared four-replica state:

```bash
docker compose up --detach --scale api=4 --wait
```

## Run checks in Docker

The test image runs formatting, linting, and unit tests:

```bash
docker compose --profile test run --build --rm api-test
```

Validate the Compose model separately with:

```bash
docker compose config --quiet
```

## Stop or reset

Normal shutdown preserves any future named-volume data:

```bash
docker compose down
```

`docker compose down --volumes` is the explicit destructive reset once later
steps add persistent observability data.

## Security note

Traefik mounts the Docker socket read-only for local container discovery. This
still grants access to sensitive Docker API metadata; read-only mounting does
not make the socket harmless. A production deployment should use a restricted
Docker API proxy or platform-native service discovery with least privilege.

# Docker Observability Learning Stack

Steps 1 through 3 provide a containerized FastAPI service behind Traefik with
structured JSON logs and end-to-end OpenTelemetry traces. Docker Compose runs
four API replicas, each with one Granian worker. Traefik and the replicas export
OTLP/gRPC spans through an OpenTelemetry Collector to Tempo. The API and
telemetry containers have no host ports; Traefik is the only published HTTP
entrypoint.

The implementation plans are recorded in
[`docs/STEP_1_IMPLEMENTATION_PLAN.md`](docs/STEP_1_IMPLEMENTATION_PLAN.md),
[`docs/STEP_2_IMPLEMENTATION_PLAN.md`](docs/STEP_2_IMPLEMENTATION_PLAN.md), and
[`docs/STEP_3_IMPLEMENTATION_PLAN.md`](docs/STEP_3_IMPLEMENTATION_PLAN.md).

## Prerequisites

- Docker Engine or Docker Desktop
- Docker Compose plugin

No host Python installation is required.

The application image is pinned to CPython 3.14.6. Direct Python dependencies
and quality tools are pinned to the latest stable versions selected for this
implementation and resolved transitively by `app/uv.lock`.

## Start the stack

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

## Inspect structured logs

Every non-health request accepts a canonical UUID in `X-Request-ID`, or gets a
generated UUID, and returns the chosen value in the same response header:

```bash
curl --include \
  --header 'X-Request-ID: 47f70a2d-2512-44ee-8f2c-0f84f5631e98' \
  'http://127.0.0.1:8080/work?units=2'
```

Inspect the application completion record and Traefik edge access record:

```bash
docker compose logs --no-log-prefix api
docker compose logs --no-log-prefix traefik
```

Application completion records contain stable service metadata, request ID,
replica/process identity, method, route template, status, outcome, and duration.
They do not contain raw URLs, query strings, headers, cookies, or bodies. Error
records add exception type and traceback frames while clients still receive a
generic response. Eligible traced requests also carry the current lowercase
`trace_id` and `span_id`, which can be used to retrieve the same trace from
Tempo. Health requests remain excluded from application logs and traces.

Granian process records use the same one-line JSON format. Its access logger is
disabled, so each eligible request has one application completion record.
Traefik emits one separately identifiable JSON edge record, retains only the
request ID correlation header, and excludes the two routine health routes.

## Inspect and verify traces

Traefik creates the edge spans and propagates W3C Trace Context to the selected
API replica. FastAPI instrumentation creates the server span, and `/work` adds
stable `demo.work.validate`, `demo.work.calculate`, and `demo.work.persist`
children. Sampling is 100% for this local learning stack.

Run the end-to-end acceptance helper:

```bash
docker compose --profile test run --build --rm trace-smoke
```

It supplies known W3C trace IDs, retrieves the resulting traces from Tempo's
internal API, verifies edge/application continuity and the manual child spans,
and fails unless repeated requests expose four distinct application
`service.instance.id` values. Its output includes verified trace IDs; search
for one in application logs to inspect log/trace correlation:

```bash
docker compose logs --no-log-prefix api | grep '<verified-trace-id>'
```

Tempo and both Collector receivers intentionally remain on the internal
`telemetry` network. Grafana access is added in Step 6. The Collector applies
memory limiting and batching, removes raw URL/query/body and sensitive header
attributes, and uses a bounded persistent retry queue before exporting to
Tempo.

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

Normal shutdown preserves Tempo traces and the Collector's persistent queue:

```bash
docker compose down
```

`docker compose down --volumes` is the explicit destructive reset. It removes
the Tempo trace store and Collector queue in addition to the containers and
networks.

## Security note

Traefik mounts the Docker socket read-only for local container discovery. This
still grants access to sensitive Docker API metadata; read-only mounting does
not make the socket harmless. A production deployment should use a restricted
Docker API proxy or platform-native service discovery with least privilege.

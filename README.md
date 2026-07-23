# Docker Observability Learning Stack

Steps 1 through 5 provide a containerized FastAPI service behind Traefik with
structured JSON logs, end-to-end OpenTelemetry traces, and OpenTelemetry
metrics stored in Prometheus, plus Docker log collection through Alloy into
Loki. Docker Compose runs four API replicas, each with one Granian worker.
Traefik and the replicas export OTLP/gRPC signals through an OpenTelemetry
Collector; traces go to Tempo and metrics go to Prometheus's internal OTLP
receiver. Alloy collects only explicitly opted-in API and Traefik containers.
The API and telemetry containers have no host ports; Traefik is the only
published HTTP entrypoint.

The implementation plans are recorded in
[`docs/STEP_1_IMPLEMENTATION_PLAN.md`](docs/STEP_1_IMPLEMENTATION_PLAN.md),
[`docs/STEP_2_IMPLEMENTATION_PLAN.md`](docs/STEP_2_IMPLEMENTATION_PLAN.md), and
[`docs/STEP_3_IMPLEMENTATION_PLAN.md`](docs/STEP_3_IMPLEMENTATION_PLAN.md),
[`docs/STEP_4_IMPLEMENTATION_PLAN.md`](docs/STEP_4_IMPLEMENTATION_PLAN.md), and
[`docs/STEP_5_IMPLEMENTATION_PLAN.md`](docs/STEP_5_IMPLEMENTATION_PLAN.md).

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

## Inspect and verify metrics

Each API replica publishes metrics every 15 seconds through the Collector. The
application supplies aggregate-friendly RED metrics for eligible HTTP requests:
request count, duration histogram, and in-flight request count. `/work` also
supplies bounded count and duration metrics. Their only data-point dimensions
are method, route template, status/outcome, and the fixed work outcome; request
and trace IDs, raw URLs/query strings, replica IDs, PIDs, and request values are
not metric labels.

Traefik additionally exports edge and backend observations. Prometheus scrapes
the Collector, Tempo, Loki, Alloy, Blackbox Exporter, and itself. The Blackbox
Exporter independently probes `/health/ready` through Traefik, so
`probe_success` remains useful when the application emits no telemetry.
Prometheus intentionally promotes only `service.name`, `service.namespace`, and
`deployment.environment.name` from OpenTelemetry resources; the per-replica
identity is not a business metric label.

Run the end-to-end acceptance helper after the stack is healthy:

```bash
docker compose --profile test run --build --rm metrics-smoke
```

It sends normal, work, slow, and failing requests through Traefik, then waits
for Prometheus to contain application RED/custom metrics, Traefik metrics, a
successful Blackbox probe, and the service RED recording rule. The helper also
fails if unsafe application labels appear. It can take a little over one
Prometheus scrape/export interval on a clean stack.

Prometheus remains internal in this step. Query it from a disposable container
on the telemetry network, for example:

```bash
docker compose exec prometheus promtool query instant http://localhost:9090 \
  'sum by (http_route) (rate(demo_http_server_request_count_total{service_name="observability-demo-api"}[5m]))'
```

The version-controlled recording rules aggregate all replica series into
service/route/method views. Validate their syntax and behavior with the pinned
Prometheus image:

```bash
docker compose run --rm --no-deps --entrypoint promtool prometheus \
  check config /etc/prometheus/prometheus.yml
docker compose run --rm --no-deps --entrypoint promtool prometheus \
  check rules /etc/prometheus/rules/api_red.yaml
docker compose run --rm --no-deps --entrypoint promtool prometheus \
  test rules /etc/prometheus/tests/api_red_test.yaml
```

To demonstrate that a process restart does not invalidate the service total,
restart one API replica, send more traffic, and re-run the metric smoke helper.
Counter values from the restarted instance may reset, but the recording rules
use `rate()` and aggregate across the independently exported replica series.

## Inspect and verify logs in Loki

Alloy discovers Docker containers through the local socket but keeps only this
Compose project's explicitly opted-in `api` and `traefik` services. Every Loki
stream has exactly three bounded labels: `service`, `environment`, and
`compose_service`. Request IDs, trace/span IDs, replica IDs, PIDs, severity,
routes, status codes, and error text remain structured metadata and JSON
content rather than indexed labels.

Run the end-to-end acceptance helper:

```bash
docker compose --profile test run --build --rm logs-smoke
```

It sends requests with known request and trace IDs, waits for Loki ingestion,
requires Traefik plus all four application replica identities, rejects any
stream label outside the allowlist, verifies correlation-field searches, and
checks that routine readiness traffic is absent.

Loki remains internal until Grafana is provisioned in Step 6. The test image
includes a small query helper that runs on the telemetry network. For example,
list recent application completion records:

```bash
docker compose --profile test run --build --rm --no-deps \
  --entrypoint python logs-smoke tests/query_logs.py \
  '{service="observability-demo-api",environment="local"} | json | event = "http_request_completed"'
```

The smoke helper output includes verified IDs. A direct LogQL correlation
query has this shape:

```logql
{service="observability-demo-api",environment="local"}
  | json
  | request_id = "<request-id>"
  | trace_id = "<trace-id>"
```

Loki uses TSDB schema v13 with local filesystem storage and seven-day
retention. Retention is time-based, not disk-usage-based, so the Loki volume
should still be monitored on a constrained workstation. Alloy persists Docker
reader state in its own named volume so a normal restart does not replay all
available container history.

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

Normal shutdown preserves Tempo traces, Prometheus metrics, Loki logs, Alloy
reader state, and the Collector's persistent queue:

```bash
docker compose down
```

`docker compose down --volumes` is the explicit destructive reset. It removes
the Tempo trace store, Prometheus TSDB, Loki log store, Alloy reader state, and
Collector queue in addition to the containers and networks.

## Security note

Traefik and Alloy mount the Docker socket read-only for local container and log
discovery. This still grants access to sensitive Docker API metadata;
read-only mounting does not make the socket harmless. Alloy runs as root
inside its otherwise capability-dropped, read-only container so it can access
the root-owned socket portably. A production deployment should use a
restricted Docker API proxy or platform-native service and log discovery with
least privilege.

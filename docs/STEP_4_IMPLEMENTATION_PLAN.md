# Step 4 Implementation Plan

This document records the plan for implementing **Step 4: OpenTelemetry
metrics and Prometheus** from `DESIGN.md`. It is intentionally created before
the implementation so the metric contracts, cardinality choices, infrastructure
configuration, and acceptance checks are reviewable afterward.

## Scope

Step 4 will add:

- per-replica OpenTelemetry metric SDK initialization and graceful shutdown;
- application HTTP RED metrics, excluding the two health endpoints;
- bounded custom `/work` operation count and duration metrics;
- OpenTelemetry metric export from each API replica through the existing
  Collector to Prometheus's native OTLP receiver;
- Traefik OTLP metrics for edge and backend observations;
- Prometheus storage, self-scraping configuration, and scrapes for the
  Collector, Tempo, and Blackbox Exporter;
- an independent Blackbox HTTP probe through Traefik;
- service-level recording rules and `promtool` rule tests; and
- automated unit/configuration/end-to-end metric checks.

Loki/Alloy log ingestion, Grafana dashboards, cAdvisor infrastructure metrics,
load generation, and alert routing remain later steps. The initial recording
rules are intentionally limited to stable service RED aggregates; alert rules
and objectives are deferred until measured lab behaviour is available.

## Metric contract and cardinality policy

Application metrics will use an OpenTelemetry meter named
`observability_demo`, versioned with the application. The following stable
metric names and dimensions will be emitted:

| Metric | Instrument | Unit | Bounded attributes |
| --- | --- | --- | --- |
| `demo.http.server.request.count` | counter | `{request}` | `http.request.method`, `http.route`, `http.response.status_code`, `event.outcome` |
| `demo.http.server.request.duration` | histogram | `s` | same HTTP attributes |
| `demo.http.server.active_requests` | up/down counter | `{request}` | `http.request.method` |
| `demo.work.count` | counter | `{operation}` | `demo.work.outcome` |
| `demo.work.duration` | histogram | `s` | `demo.work.outcome` |

The HTTP middleware will resolve the FastAPI route template after request
handling, use a bounded allowlist for methods, return the existing bounded
outcome classification, and never place a raw path, query string, request ID,
trace ID, user value, exception text, PID, or instance ID on a metric point.
The active-request instrument uses only the method because it is incremented
before route matching; it will be decremented in `finally`, including for
unexpected application failures. `/health/live` and `/health/ready` will not
emit application HTTP metrics.

The custom `/work` measurements will time the complete business operation and
record one `success` outcome only after it completes. Its validation bounds
continue to protect both the host and metric cardinality.

All metric points share the existing OpenTelemetry resource identity:
`service.name`, `service.namespace`, `service.version`,
`service.instance.id`, and `deployment.environment.name`. Prometheus will
promote only `service.name`, `service.namespace`, and
`deployment.environment.name`; replica identity remains resource metadata and
is not a normal business-series label.

## Planned repository changes

1. Add an application metrics runtime.
   - Build a `MeterProvider` from the same resource used by tracing.
   - Use an OTLP/gRPC periodic metric reader when metrics are enabled, with the
     15-second interval configured through standard OpenTelemetry environment
     variables.
   - Keep a no-op meter when disabled so existing isolated tests do not require
     an OTLP endpoint.
   - Force-flush and shut down the metric provider once during FastAPI
     lifespan teardown alongside the trace provider.

2. Instrument the HTTP middleware and `/work` endpoint.
   - Record HTTP count, duration in base seconds, and active request count only
     for eligible application routes.
   - Record custom business count and duration with a fixed `success` or
     `error` outcome.
   - Reuse existing bounded route/method/outcome helpers and cover the emitted
     attributes, units, health exclusion, concurrency, and error paths with an
     in-memory metric reader.

3. Extend the Collector metric pipeline.
   - Reuse its OTLP gRPC/HTTP receiver, safety processor, memory limiter, and
     batch processor.
   - Export metrics via OTLP/HTTP to Prometheus's internal OTLP receiver with a
     bounded persistent retry queue.
   - Expose Collector internal Prometheus-format telemetry on an internal
     endpoint for Prometheus to scrape.

4. Add Prometheus and Blackbox Exporter services and version-controlled
   configuration.
   - Pin compatible images; mount configurations read-only; use named storage
     for the Prometheus TSDB; and keep their APIs internal for this step.
   - Enable the Prometheus OTLP receiver with resource-attribute promotion
     limited to the reviewed service/environment identity fields.
   - Scrape Prometheus itself, the Collector, Tempo, and Blackbox Exporter.
   - Configure Blackbox Exporter's HTTP module to probe Traefik's public
     service path from the Docker edge network, with a fixed successful status
     expectation and no secret-bearing parameters.

5. Enable Traefik metrics.
   - Configure its OpenTelemetry metrics exporter to the Collector with the
     same local 15-second export cadence.
   - Keep edge service identity distinct from the application and leave health
     routers excluded, preserving a clean comparison of edge and application
     traffic.

6. Add recording rules, tests, smoke coverage, and documentation.
   - Record aggregate service request rate, error rate, duration histogram
     buckets, and active request count from the application metrics without a
     replica label.
   - Add `promtool` rule tests proving aggregation across replica-labelled
     input data and correct error/rate math.
   - Add an end-to-end metric smoke helper that generates normal, slow, error,
     and work traffic through Traefik, waits for Prometheus ingestion, and
     verifies application RED/custom metrics, edge metrics, and probe success.
   - Update the README with startup, Prometheus query, metric smoke, recording
     rule validation, and preserved-volume guidance.

## Verification plan

1. Refresh the locked Python dependency graph and run Ruff plus unit tests in
   the application test image.
2. Run `promtool check config`, `promtool check rules`, and `promtool test
   rules` in the pinned Prometheus image.
3. Validate resolved Compose configuration and start the stack with four API
   replicas, Traefik, Collector, Tempo, Prometheus, and Blackbox Exporter.
4. Generate eligible requests through Traefik and query Prometheus's API until
   the application count, duration histogram, active-request metric, custom
   work metrics, Traefik edge metrics, and Blackbox `probe_success` are
   present.
5. Verify query dimensions contain only the documented bounded labels and
   promoted service/environment resource labels, with no raw URL, request ID,
   trace ID, user input, PID, or replica ID business label.
6. Restart one API replica, generate additional traffic, and verify service
   totals/rates remain valid aggregate series rather than becoming a
   per-process total.

If Docker or registry access prevents the end-to-end checks, local formatting,
linting, unit tests, resolved Compose validation, and image-local `promtool`
checks will still run. Any unavailable acceptance check will be stated in the
implementation handoff.

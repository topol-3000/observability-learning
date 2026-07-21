# Step 3 Implementation Plan

This document records the plan for implementing **Step 3: OpenTelemetry
traces** from `DESIGN.md`. It is created before the implementation so the
instrumentation boundaries, data-safety decisions, infrastructure changes, and
acceptance checks remain reviewable afterward.

## Scope

Step 3 will add:

- one OpenTelemetry tracing SDK and batch span processor per API replica;
- W3C Trace Context extraction and FastAPI server-span instrumentation;
- trace/span correlation on the existing structured application completion
  logs;
- bounded manual child spans for the `/work` operation;
- OTLP/gRPC trace export from the API and Traefik to an OpenTelemetry
  Collector;
- Traefik edge spans and propagation of their context to the selected API
  replica;
- a Collector trace pipeline with memory limiting, batching, bounded retry and
  queue behavior, and OTLP export;
- a single-node Tempo trace store with local persistent storage; and
- automated unit/configuration checks plus an end-to-end acceptance helper.

Metrics, Prometheus, Loki/Alloy, Grafana provisioning, exemplars, dashboards,
and tail sampling remain out of scope until their later design steps.

## Planned repository changes

1. Add a focused application tracing module.
   - Build an OpenTelemetry `Resource` from the existing service name, version,
     environment, and replica-start UUID.
   - Create a parent-based, always-on sampler for deterministic local learning.
   - Export with OTLP/gRPC through a `BatchSpanProcessor`; keep backend details
     configurable through standard OpenTelemetry environment variables.
   - Initialize at most once per application instance and shut the provider
     down during the FastAPI lifespan so queued spans receive a bounded flush
     opportunity.
   - Keep the default global provider untouched when tracing is explicitly
     disabled, allowing isolated unit tests without an exporter.

2. Instrument FastAPI requests and application logs.
   - Use the maintained FastAPI instrumentation package to extract incoming
     W3C context and create server spans with semantic HTTP attributes.
   - Exclude `/health/live` and `/health/ready` from instrumentation to avoid
     probe noise.
   - Read the current valid span when writing the existing completion log and
     bind lowercase trace/span IDs only for that record; do not trust incoming
     trace headers as log fields directly.
   - Mark genuine 5xx operations as errors and record handled application
     exceptions without attaching exception messages or request data.

3. Add manual `/work` child spans.
   - Create stable spans for validation, calculation, and persistence
     simulation boundaries.
   - Attach only reviewed bounded attributes such as work units and a bounded
     outcome; never attach request IDs, raw URLs, query strings, calculated
     values, secrets, or other unbounded content.
   - Keep span names independent of request values so trace cardinality remains
     bounded.

4. Add the Collector and Tempo services.
   - Pin explicit container image versions and mount version-controlled
     configurations read-only.
   - Configure the Collector with OTLP gRPC/HTTP receivers, `memory_limiter`
     before `batch`, a bounded sending queue and retry policy, internal health
     checking, and OTLP/gRPC export to Tempo.
   - Configure Tempo for local single-binary storage on a named volume and an
     internal-only OTLP receiver/readiness endpoint.
   - Keep all unauthenticated receivers and backend APIs off host-published
     ports, add health-aware startup ordering, and apply practical local
     hardening without preventing required data/WAL writes.

5. Enable Traefik tracing and propagation.
   - Configure OTLP/gRPC export to the Collector at 100% sampling.
   - Give edge telemetry its own service name and shared namespace/environment
     resource attributes.
   - Disable tracing on health routers while leaving it enabled for eligible
     API traffic.
   - Preserve the incoming or generated W3C context across the edge and API so
     both layers appear in one trace.

6. Extend tests and operator documentation.
   - Unit-test resource identity, instrumentation exclusions, log correlation,
     context isolation, stable manual-span names/attributes, parentage, and
     error status/exception recording with an in-memory exporter.
   - Add static validation/startup checks for Collector and Tempo
     configuration where their pinned images support them.
   - Document startup, trace generation, Tempo API inspection, correlation,
     shutdown, and troubleshooting commands.

## Trace data policy

Application-created spans may include only stable operation names and reviewed
OpenTelemetry HTTP/resource attributes. The custom `/work` spans may additionally
include the bounded integer `demo.work.units` and fixed outcome values.

Request/response bodies, authorization and cookie headers, arbitrary request
headers, raw query strings, client-supplied request IDs, exception messages,
checksums, user identifiers, and secrets must not be attached to spans.
`service.instance.id` is resource metadata used to distinguish replicas; it is
not copied onto every span as a custom attribute.

## Verification plan

1. Refresh and verify the locked Python dependency graph, then run Ruff
   formatting/linting and all unit tests in the dedicated test image.
2. Validate the resolved Compose model and the pinned Collector, Tempo, and
   Traefik configurations.
3. Start Traefik, four API replicas, the Collector, and Tempo and wait for
   health checks.
4. Send one `/work` request with a known request ID and verify through Tempo's
   API that one trace contains a Traefik edge span, an application server span,
   and the expected manual child spans with correct parentage.
5. Verify the corresponding application completion log contains the same trace
   ID and its current application span ID, without sensitive inputs.
6. Send repeated requests until traces contain all four distinct application
   `service.instance.id` values.
7. Exercise `/error` and confirm the application operation is marked as an
   error while its client response remains generic and exception messages do
   not enter reviewed custom attributes.
8. Stop the stack gracefully, inspect API/Collector/Traefik shutdown logs for
   exporter errors or dropped spans, and preserve the Tempo volume by default.

If Docker or registry/network availability blocks an end-to-end check, local
formatting, linting, unit tests, static Compose validation, and any available
image-local configuration validation will still run. The exact unverified
acceptance item will be documented in the implementation handoff.

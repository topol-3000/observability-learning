# Step 5 Implementation Plan

This document records the plan for implementing **Step 5: Loki and Alloy**
from `DESIGN.md`. It is intentionally created before the runtime
implementation so the log-selection rules, label policy, retention choice,
security exceptions, and acceptance checks remain reviewable afterward.

## Scope

Step 5 will add:

- explicit Docker discovery of the Traefik and scaled API containers;
- collection of their stdout/stderr streams with Grafana Alloy;
- JSON parsing for both application and Traefik log records;
- a strict, low-cardinality Loki label set;
- single-node Loki storage with laptop-appropriate retention;
- Prometheus scrapes for Loki and Alloy internal metrics;
- an end-to-end smoke check for ingestion, replica coverage, correlation
  searches, and label safety; and
- operator documentation for querying and troubleshooting the log pipeline.

Grafana data-source provisioning, derived fields, dashboards, trace-to-log
links, and trace-to-metric correlation remain Step 6 work. Step 5 queries Loki
through its internal HTTP API so no unauthenticated log backend port needs to
be published to the host.

## Log selection and label policy

Only containers that explicitly opt in with a reviewed Compose label will be
collected. Alloy will additionally constrain discovery to this Compose project
and the `api` or `traefik` Compose services. This avoids accidentally ingesting
logs from Alloy itself, observability backends, test jobs, or unrelated Docker
workloads visible through the Docker socket.

Every ingested stream will have exactly these user-controlled Loki labels:

| Label | Source | Expected values |
| --- | --- | --- |
| `service` | explicit Compose label | `observability-demo-api`, `observability-demo-edge` |
| `environment` | explicit Compose label | `local` by default |
| `compose_service` | Docker Compose metadata | `api`, `traefik` |

Alloy may add Loki's standard internal labels while processing, but temporary
Docker discovery labels will not be forwarded as stream labels. Container
name/ID, replica identity, process PID, logger, severity, route, status, error
text, request ID, trace ID, and span ID will not be indexed labels.

Application records will remain JSON log lines, so `request_id`, `trace_id`,
`span_id`, and `service.instance.id` can be parsed and filtered at query time.
Traefik JSON records receive the same stream labels but keep their reviewed
native fields. JSON parsing failures will be retained as log content rather
than silently discarded, while routine health-request records remain excluded
at their existing application/Traefik sources.

## Planned repository changes

1. Add a single-node Loki configuration.
   - Use TSDB schema v13 with filesystem object storage on a named Docker
     volume.
   - Disable multi-tenancy for the internal-only local learning stack.
   - Enable the Compactor and enforce seven-day retention, with bounded
     deletion concurrency suitable for a laptop.
   - Keep ingestion/query limits explicit, disable usage reporting, and expose
     Loki readiness and internal metrics only on the telemetry network.

2. Add an Alloy configuration for Docker logs.
   - Discover containers through the local Docker socket.
   - Keep only explicitly opted-in `api` and `traefik` containers from this
     Compose project.
   - Map only the reviewed service, environment, and Compose-service metadata
     into Loki labels;
   - read Docker log streams, parse each line as JSON, and forward records to
     Loki's push endpoint;
   - persist Alloy reader positions on a named volume so normal container
     restarts do not replay the full available Docker log history; and
   - expose Alloy internal metrics on the telemetry network.

3. Extend Docker Compose.
   - Pin compatible Loki and Alloy image versions.
   - Mount both version-controlled configurations read-only and give their
     writable state dedicated named volumes.
   - Extend the one-shot storage initializer with the required ownership for
     Loki and Alloy's runtime users.
   - Keep both HTTP endpoints internal, add readiness-aware startup ordering,
     resource limits, read-only root filesystems, dropped capabilities, and
     `no-new-privileges`.
   - Mount the Docker socket read-only into Alloy as an explicitly documented
     local-learning exception; acknowledge that this still exposes sensitive
     Docker API metadata.
   - Add reviewed opt-in/service/environment labels only to Traefik and API.

4. Add pipeline monitoring and acceptance coverage.
   - Add Prometheus scrape jobs for Loki and Alloy.
   - Add a test-profile log smoke service that sends known request and trace
     IDs through Traefik, waits for Loki ingestion, and queries Loki's HTTP
     API.
   - Require logs from Traefik and all four distinct API replica identities.
   - Verify request-ID and trace-ID searches return the expected application
     record while neither correlation field nor replica/process identity is a
     stream label.
   - Fail if any stream contains labels outside the documented allowlist.

5. Update operator documentation.
   - Record the Step 5 plan alongside earlier plans.
   - Document the seven-day retention window, named volumes, Docker socket
     risk, smoke command, internal Loki query examples, and preserved/reset
     data behavior.
   - Explain that request/trace correlation uses JSON filters in Step 5 and
     becomes clickable Grafana correlation in Step 6.

## Verification plan

1. Validate the resolved Compose model and confirm only Traefik and API opt in
   to Docker log collection.
2. Run Alloy's configuration formatter/check and Loki's version-specific
   configuration validation using the pinned container images.
3. Run the existing application Ruff, unit, Prometheus rule, distribution,
   trace, and metric checks to guard earlier steps.
4. Start the full stack from clean Loki/Alloy volumes and wait for Loki, Alloy,
   Traefik, four API replicas, and existing telemetry services to become
   healthy.
5. Run the log smoke helper and verify both service streams, all four API
   replica identities, known request/trace searches, the exact label allowlist,
   and the absence of health-log noise.
6. Query Prometheus for healthy Loki/Alloy scrape targets and inspect their
   ingestion/export failure metrics.
7. Restart Alloy and one API replica, generate another request, and confirm
   collection resumes without invalid labels or service interruption.

If Docker or registry access prevents image-local or end-to-end checks, local
formatting, unit tests, static configuration review, and resolved Compose
validation will still run. Any unavailable acceptance check will be stated in
the implementation handoff.

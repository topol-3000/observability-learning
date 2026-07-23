# Step 6 Implementation Plan

This document records the plan for implementing **Step 6: Grafana
provisioning and correlation** from `DESIGN.md`. It is intentionally created
before the runtime implementation so the dashboard scope, correlation
contracts, local-access policy, and acceptance checks remain reviewable
afterward.

## Scope

Step 6 will add:

- a pinned Grafana container with persistent local state and a loopback-only
  host port;
- version-controlled Prometheus, Loki, and Tempo data sources with stable
  UIDs;
- a read-only provisioned service dashboard focused on symptoms and
  investigation;
- bidirectional log/trace navigation;
- trace-to-metrics navigation and Prometheus metric exemplars linked to Tempo;
- a clean-start provisioning and end-to-end correlation smoke check; and
- operator documentation for the dashboard and investigation workflow.

cAdvisor resource panels, reproducible k6 load scenarios, SLO objectives,
alerting, and failure labs remain Steps 7 through 9. The Step 6 dashboard will
show the application, edge, synthetic probe, and telemetry-pipeline signals
that already exist, without inventing unavailable container metrics.

## Provisioning and access policy

Grafana will use stable data-source UIDs (`prometheus`, `loki`, and `tempo`) and
a stable dashboard UID so links survive volume resets and configuration
reloads. Data sources and dashboards will be mounted read-only from
`observability/grafana/provisioning`.

The dashboard provider will disable UI saves. The checked-in JSON is the source
of truth, and updates must be made in version control rather than only in the
Grafana database. Grafana's writable database and runtime data will live in a
named volume.

Grafana will bind its host port to `127.0.0.1` and enable anonymous Viewer
access for this local-only learning environment. No default admin password or
production credential will be committed. Sign-up, anonymous editing, usage
reporting, update checks, and plugin administration will be disabled. The
Prometheus, Loki, and Tempo endpoints will remain internal; browser queries
will pass through Grafana's server-side data-source proxy.

## Correlation contracts

### Logs to traces

The Loki data source will define a derived field that extracts the lowercase
32-character `trace_id` from each JSON log line and opens that ID in the
provisioned Tempo data source. The trace ID remains log content/structured
metadata rather than a Loki stream label.

### Traces to logs

The Tempo data source will query Loki with:

- the span resource attribute `service.name` mapped to the bounded Loki
  `service` label;
- a small time window around the span;
- trace-ID filtering enabled; and
- span-ID filtering disabled, because request completion logs are correlated
  at trace level and may be emitted in the context of a different span than
  the selected child span.

This produces a bounded service selector plus a parsed JSON trace-ID filter,
not a high-cardinality Loki label.

### Traces to metrics

The Tempo data source will map `service.name` to Prometheus's promoted
`service_name` label and provide custom links for application request rate,
error rate, and duration. The queries will use the existing aggregate-friendly
application RED metrics and a window around the selected span.

### Metrics to traces

The Prometheus data source will map the `trace_id` exemplar label to Tempo.
Prometheus exemplar storage will be explicitly enabled. The existing
trace-based OpenTelemetry SDK exemplar path, Collector OTLP export, and
Prometheus native OTLP receiver will then be acceptance-tested end to end. If
the pinned versions do not preserve a real trace exemplar, this direction will
not be documented as working merely because the Grafana link is configured.

## Dashboard design

The provisioned **Observability Demo Service** dashboard will use the fixed
local environment and the existing bounded route label. It will contain:

1. externally observed readiness and healthy Traefik backend counts;
2. application request rate and server-error ratio by route;
3. application p50/p95/p99 latency with exemplars enabled;
4. edge request rate and edge-versus-application p95 latency;
5. application in-flight requests and backend health/distribution;
6. recent application error logs from Loki;
7. recent slow or error traces from Tempo; and
8. Prometheus target health plus Collector, Loki, and Alloy
   refusal/drop/failure indicators.

Panels will prefer existing recording rules where they preserve the needed
dimensions. Raw histograms will be aggregated correctly before
`histogram_quantile`. Empty periods will remain visibly empty where absence is
meaningful; panels will not turn missing telemetry into a healthy zero.

## Planned repository changes

1. Add Grafana provisioning.
   - Provision Prometheus, Loki, and Tempo with stable UIDs and internal
     service URLs.
   - Configure Loki derived fields, Tempo trace-to-logs, Tempo
     trace-to-metrics, and Prometheus exemplar links.
   - Provision a file-backed dashboard provider with UI updates disabled.
   - Add the focused dashboard JSON with stable panel IDs and data-source UIDs.

2. Extend Docker Compose.
   - Pin the current tested stable Grafana OSS image.
   - Publish Grafana only on a configurable loopback host port.
   - Mount provisioning files read-only and add a named Grafana data volume.
   - Extend the storage initializer with Grafana's runtime ownership.
   - Add backend health dependencies, a health check, a read-only root
     filesystem, temporary writable paths, dropped capabilities,
     `no-new-privileges`, and bounded resources.
   - Enable Prometheus exemplar storage explicitly.

3. Add acceptance coverage.
   - Add a test-profile smoke service that sends deliberately slow and failed
     requests with known W3C trace and request IDs.
   - Verify Grafana health, the three stable data-source UIDs, correlation
     settings, and the provisioned dashboard through Grafana's HTTP API.
   - Query Loki, Tempo, and Prometheus through Grafana's data-source proxy.
   - Require the known trace in Tempo, its exact application log in Loki, and
     the same trace ID on a Prometheus duration exemplar.
   - Check that the dashboard contains the expected symptom, log, trace, and
     pipeline panels and only references provisioned data sources.

4. Update operator documentation.
   - Add Step 6 to the implemented stack summary and implementation-plan list.
   - Document the Grafana URL, local anonymous Viewer policy, clean-volume
     smoke command, dashboard sections, and symptom-to-trace-to-log workflow.
   - Explain that provisioned dashboards are intentionally read-only and that
     container saturation panels arrive in Step 7.

## Verification plan

1. Validate JSON/YAML syntax and the resolved Compose model.
2. Run Grafana's pinned image against the mounted provisioning files and
   inspect startup logs for provisioning or dashboard errors.
3. Run the existing application formatting, lint, unit, Prometheus rule,
   distribution, trace, metric, and log checks to protect Steps 1 through 5.
4. Start the complete stack from empty Grafana and Prometheus volumes, wait for
   health, and run the new Grafana correlation smoke helper.
5. Confirm the known slow/failed requests appear in the dashboard's time
   window, open a metric exemplar in Tempo, follow a span to its Loki logs,
   and follow the log-derived trace link back to Tempo.
6. Recreate Grafana with its state volume removed and re-run the smoke check to
   prove no manual UI setup is required.

If Docker or registry access prevents image-local or end-to-end checks, static
configuration validation and the existing containerized test suite will still
run. Any unavailable acceptance check will be stated in the implementation
handoff.

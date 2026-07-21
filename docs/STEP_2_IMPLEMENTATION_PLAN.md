# Step 2 Implementation Plan

This document records the plan for implementing **Step 2: Structured logging
and context** from `DESIGN.md`. It is created before the implementation so the
scope, security decisions, and acceptance checks remain reviewable afterward.

## Scope

Step 2 will add:

- single-line JSON application logs written to stdout;
- request ID acceptance, validation, generation, context binding, and response
  propagation;
- trace and span context fields that are ready for Step 3 instrumentation but
  omitted when no current trace exists;
- one bounded application completion event for every non-health request;
- useful structured exception context without exposing exception messages to
  clients;
- JSON-compatible Granian process logging without duplicate access records;
- Traefik JSON access logs with a reviewed field and header set; and
- tests for log shape, correlation, exclusions, exceptions, and secret
  redaction.

OpenTelemetry SDK setup, trace creation/export, Docker log collection, Loki,
and Grafana correlation remain out of scope until their later design steps.

## Planned repository changes

1. Add a focused logging module under `app/src/observability_demo`.
   - Implement a standard-library JSON formatter that emits exactly one JSON
     object per line.
   - Include UTC/RFC 3339 timestamps, severity, logger, event, service name,
     service version, environment, replica identity, and process PID.
   - Bind request ID, trace ID, and span ID through `contextvars`, so concurrent
     requests cannot leak correlation data into each other.
   - Keep trace/span values optional for Step 2 and expose a small binding API
     for the OpenTelemetry work in Step 3.
   - Serialize exception type and stack trace as separate fields while avoiding
     accidental serialization of arbitrary `LogRecord` attributes.

2. Add HTTP request-context middleware to the FastAPI application.
   - Accept `X-Request-ID` only when it matches a conservative ASCII format and
     bounded length; otherwise generate a UUID.
   - Return the accepted/generated ID in the response header.
   - Emit one `http_request_completed` record after each eligible request with
     method, route template, status code, outcome, and duration.
   - Use route templates rather than raw URLs, and never record query strings,
     request/response bodies, cookies, authorization headers, or arbitrary
     headers.
   - Exclude `/health/live` and `/health/ready` from application request logs.
   - Preserve generic client-facing 500 responses while logging exception type
     and traceback for server failures.

3. Configure application and Granian logging explicitly at startup.
   - Send application and server lifecycle records through the same JSON
     formatter on stdout.
   - Keep Granian's own access log disabled so it cannot duplicate the
     application completion record.
   - Prevent handler propagation/duplication when the app factory is created
     repeatedly in tests.

4. Configure Traefik JSON access logging.
   - Emit one edge access record per request in JSON format.
   - Keep a reviewed set of request/response fields and the request ID response
     header used for correlation; drop all other headers by default.
   - Drop routine health-probe access records using an access-log filter.
   - Keep Traefik operational logs in JSON as well.

5. Extend tests and operator documentation.
   - Capture and parse application output as JSON rather than relying on text
     fragments.
   - Verify required stable fields, one completion event, request ID round-trip,
     generated replacement of invalid IDs, route-template logging, health-log
     exclusion, and context isolation under concurrent requests.
   - Verify error records contain an exception type and stack trace while client
     responses remain generic.
   - Send representative authorization, cookie, query, and body secrets and
     prove none occur in captured logs.
   - Document the fields, correlation behavior, exclusions, and commands for
     inspecting API and edge logs.

## Bounded field policy

Application request completion records will contain only:

- `http.request.method`;
- `http.route` (the matched FastAPI route template, or a fixed fallback);
- `http.response.status_code`;
- `event.outcome` (`success`, `client_error`, or `server_error`); and
- `duration_ms` as a numeric value.

Request ID, trace ID, span ID, replica ID, PID, exception type, and stack trace
remain structured JSON fields, not future metric dimensions or Loki labels.
No raw path, query string, network address, user agent, request body, response
body, or unreviewed header is added.

## Verification plan

1. Run Ruff formatting and lint checks plus all application unit tests in the
   existing locked environment and in the dedicated Docker test target.
2. Validate the resolved Compose model with `docker compose config --quiet`.
3. Start Traefik and four API replicas and wait for container health.
4. Exercise normal, validation-error, intentional-error, secret-bearing, and
   health requests through Traefik.
5. Parse emitted API and Traefik lines as JSON and verify required fields,
   correlation IDs, health exclusions, and absence of supplied secrets.
6. Verify each eligible request produces exactly one edge access record and
   exactly one application completion record, without within-layer duplicates.
7. Re-run the four-replica distribution smoke test to ensure the Step 1 routing
   behavior remains intact.

If Docker or image availability blocks an end-to-end check, formatting,
linting, unit tests, and static Compose validation will still run and the exact
unverified acceptance item will be documented.

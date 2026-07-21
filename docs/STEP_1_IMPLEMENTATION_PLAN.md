# Step 1 Implementation Plan

This document records the plan for implementing **Step 1: Containerized
FastAPI/Granian baseline** from `DESIGN.md`. It is intentionally committed as a
separate artifact before the application implementation so the intended scope,
decisions, and verification remain reviewable afterward.

## Scope

Step 1 will provide:

- a small FastAPI application with the seven routes described in the design;
- bounded inputs for simulated work and latency;
- explicit liveness and readiness behavior;
- graceful application lifespan handling;
- one Granian ASGI worker in each API container;
- a non-root, multi-stage application image with a container health check;
- locked Python dependencies;
- four declaratively scaled API replicas in Docker Compose;
- Traefik Docker discovery, health-aware routing, and the only published API
  port; and
- application unit tests that run in a container.

Telemetry instrumentation, structured JSON logging, observability backends,
dashboards, and alerts remain out of scope until their later design steps.

## Planned repository changes

1. Create the Python package under `app/src/observability_demo`.
   - Build a FastAPI application with `/`, `/work`, `/slow`, `/error`,
     `/debug/instance`, `/health/live`, and `/health/ready`.
   - Return replica hostname and process ID from the debug endpoint so routing
     across containers can be verified.
   - Keep delay/work controls bounded and return a generic response for the
     intentional error route.
   - Use the FastAPI lifespan API to model startup/readiness and graceful
     shutdown.

2. Add unit tests under `app/tests`.
   - Cover normal responses, bounded inputs, health/readiness, replica
     identity, and the intentional error response.
   - Keep tests independent of Docker networking so they run quickly and
     deterministically.

3. Add dependency and quality configuration in `app/pyproject.toml` and a
   generated `app/uv.lock`.
   - Separate runtime and development dependencies.
   - Pin CPython to 3.14.6 and configure pinned stable pytest and Ruff versions
     so the containerized test target can run formatting, linting, and unit
     checks consistently.
   - Use Python 3.14's native deferred annotation evaluation without a
     compatibility future import. Do not configure a separate static type
     checker.

4. Build `app/Dockerfile` as a multi-stage image.
   - Install only locked runtime dependencies in the final stage.
   - Copy application code into an unprivileged runtime image.
   - Run Granian on port 8000 with exactly one worker and explicit graceful
     shutdown/backpressure settings supported by the pinned Granian version.
   - Include a Python-based health check without adding a separate HTTP client.
   - Provide a dedicated test image target containing development tools and
     tests.

5. Add Traefik static configuration and `compose.yaml`.
   - Pin image versions and avoid `container_name` on the scalable API service.
   - Set `scale: 4`, expose the API only to the internal network, and
     publish only Traefik on the host.
   - Configure `exposedByDefault=false`, explicit router/service labels, and an
     HTTP backend health check against `/health/ready`.
   - Apply practical local hardening: read-only filesystems, temporary filesystems,
     dropped capabilities, `no-new-privileges`, init processes, bounded stop
     grace periods, and per-replica resource limits where supported.
   - Document the Docker socket exception required for local Traefik discovery.

6. Add operator documentation and safe defaults.
   - Provide `.env.example` for the published HTTP port and application version.
   - Add a concise `README.md` with build/start, distribution, failure,
     containerized-test, shutdown, and troubleshooting commands.
   - Add `.dockerignore`/`.gitignore` entries needed for reproducible builds.

## Verification plan

The implementation will be checked in increasing scope:

1. Generate/validate the dependency lock and inspect the pinned runtime
   versions.
2. Build the dedicated test image and run formatting, linting, and unit tests
   inside it.
3. Validate the fully resolved Compose model with `docker compose config` and
   confirm it declares four API replicas, no API host port, and one Granian
   worker per replica.
4. Build and start the baseline with Docker Compose.
5. Wait for four healthy API replicas and a reachable Traefik endpoint.
6. Send repeated requests through Traefik until all four replica identities are
   observed.
7. Stop one API replica, verify the public endpoint remains available, and
   verify the stopped replica is no longer returned by routing.
8. Stop the stack without deleting volumes or unrelated Docker resources.

If the local environment cannot perform a network-dependent image/dependency
download, the remaining static and local checks will still run and the exact
blocked acceptance check will be documented.

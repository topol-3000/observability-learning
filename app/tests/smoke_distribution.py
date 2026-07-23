"""Verify that Traefik distributes requests across the expected replicas."""

import json
import time
import urllib.error
import urllib.request

from observability_demo.settings import DistributionSmokeSettings

settings = DistributionSmokeSettings()
URL = settings.url
EXPECTED_REPLICAS = settings.expected_replicas
DEADLINE_SECONDS = settings.deadline_seconds


def fetch_instance() -> str:
    request = urllib.request.Request(URL, headers={"Connection": "close"})
    with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
        payload = json.load(response)
    return str(payload["instance_id"])


def main() -> None:
    deadline = time.monotonic() + DEADLINE_SECONDS
    instances: set[str] = set()
    last_error: Exception | None = None

    while time.monotonic() < deadline and len(instances) < EXPECTED_REPLICAS:
        try:
            instances.add(fetch_instance())
            last_error = None
        except (OSError, KeyError, ValueError, urllib.error.HTTPError) as error:
            last_error = error
            time.sleep(0.2)

    print(json.dumps({"observed_instances": sorted(instances)}))
    if len(instances) != EXPECTED_REPLICAS:
        message = f"expected {EXPECTED_REPLICAS} replicas, observed {len(instances)}"
        if last_error is not None:
            message = f"{message}; last request error: {last_error}"
        raise SystemExit(message)


if __name__ == "__main__":
    main()

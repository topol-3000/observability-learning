"""Run a recent LogQL query against the stack's internal Loki API."""

import argparse
import json
import os
import time
import urllib.parse
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="LogQL stream selector and pipeline")
    parser.add_argument("--minutes", type=int, default=5)
    arguments = parser.parse_args()

    loki_url = os.environ.get("LOKI_URL", "http://loki:3100")
    now_ns = time.time_ns()
    parameters = urllib.parse.urlencode(
        {
            "query": arguments.query,
            "start": now_ns - arguments.minutes * 60 * 1_000_000_000,
            "end": now_ns,
            "direction": "backward",
            "limit": 1000,
        }
    )
    with urllib.request.urlopen(  # noqa: S310
        f"{loki_url}/loki/api/v1/query_range?{parameters}",
        timeout=10,
    ) as response:
        print(json.dumps(json.load(response), indent=2))


if __name__ == "__main__":
    main()

"""Publish a fresh camera health Fact to a SafeExec Runtime."""

from __future__ import annotations

import argparse
import json
import time
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8787")
    parser.add_argument("--ttl-ms", type=int, default=1500)
    args = parser.parse_args()
    payload = {
        "key": "camera.healthy",
        "value": True,
        "source": "rdk-camera",
        "confidence": 1.0,
        "timestamp": time.time(),
        "ttl_ms": args.ttl_ms,
        "evidence": {"mode": "manual-integration-check"},
    }
    request = Request(
        f"{args.runtime_url.rstrip('/')}/v1/facts",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        result = json.loads(response.read().decode("utf-8"))
    print(json.dumps(result, indent=2), flush=True)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

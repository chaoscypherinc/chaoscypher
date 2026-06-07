"""All-in-one container liveness probe.

Hits Cortex's ``/health`` endpoint directly on the workload port,
bypassing nginx and TLS. Mirrors the multi-container Cortex
healthcheck so both deployment shapes use the same pattern.

Why bypass nginx?
- Docker HEALTHCHECK is semantically a liveness probe ("is the
  workload alive?"), and Docker's response to "unhealthy" is to
  restart the container. The right signal for that is process
  liveness, not nginx config correctness or TLS cert validity.
- Coupling the probe to nginx means a brittle interaction with
  HTTP→HTTPS redirects, self-signed cert verification, etc.
- nginx, valkey, and neuron crashes are independently restarted by
  supervisord — the Docker HEALTHCHECK doesn't need to gate them.
- Aggregate readiness ("is Valkey ready? is Neuron ready?") is
  visible in the operator UI; that's the appropriate surface for
  it, not a Docker boolean.

Exit codes:
    0: Cortex /health responded
    1: Cortex /health failed (process down, port unreachable, etc.)
"""

import os
import sys
import urllib.request


timeout = int(os.environ.get("CHAOSCYPHER_HEALTH_TIMEOUT", "5"))
port = int(os.environ.get("CHAOSCYPHER_HEALTH_PORT", "8080"))


try:
    url = f"http://localhost:{port}/health"
    urllib.request.urlopen(url, timeout=timeout)  # noqa: S310 - localhost only
    sys.exit(0)

except Exception as e:
    print(f"unhealthy: {e}", file=sys.stderr)  # noqa: T201 - Docker healthcheck output
    sys.exit(1)

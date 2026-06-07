"""Cortex healthcheck script.

Simple HTTP check against the /health endpoint.
Used by Docker HEALTHCHECK to verify the Cortex API is responsive.

Exit codes:
    0: Healthy (HTTP 200 from /health)
    1: Unhealthy (connection error or non-200 response)
"""

import os
import sys
import urllib.request


port = os.environ.get("CHAOSCYPHER_API_PORT", "8080")
timeout = int(os.environ.get("CHAOSCYPHER_HEALTH_TIMEOUT", "5"))

try:
    urllib.request.urlopen(f"http://localhost:{port}/health", timeout=timeout)
    sys.exit(0)
except Exception:
    sys.exit(1)

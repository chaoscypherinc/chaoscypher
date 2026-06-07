"""Neuron worker healthcheck script.

Checks if the QueueWorker is alive by verifying that Valkey health keys
exist for both queues. The worker publishes to ``queue:{name}:health``
every 2 seconds with a 10-second TTL, so keys auto-expire if the worker
dies.

Exit codes:
    0: Healthy (both health keys exist)
    1: Unhealthy (one or both keys missing)
"""

import os
import sys

import valkey


host = os.environ.get("QUEUE_HOST", "valkey")
port = int(os.environ.get("QUEUE_PORT", "6379"))
db = int(os.environ.get("QUEUE_DB", "0"))
password = os.environ.get("QUEUE_PASSWORD") or None

try:
    connect_timeout = int(os.environ.get("CHAOSCYPHER_HEALTH_TIMEOUT", "3"))
    r = valkey.Valkey(
        host=host, port=port, db=db, password=password, socket_connect_timeout=connect_timeout
    )
    keys_exist = r.exists("queue:llm:health", "queue:operations:health")
    sys.exit(0 if keys_exist == 2 else 1)
except Exception:
    sys.exit(1)

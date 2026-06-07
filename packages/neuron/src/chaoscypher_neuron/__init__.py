# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chaos Cypher Neuron - Background Task Processing Workers.

Background task processing capabilities with a unified worker that handles
both LLM and Operations queues concurrently. Part of the Neural Architecture.

Queue Configuration:
    - LLM queue: Serialized AI operations (default 1 concurrent task)
      (chat, embeddings, tool calls, chunk extraction)
    - Operations queue: Parallel processing (default 8 concurrent tasks)
      (source processing, exports, workflows, bulk operations)

Components:
    - worker: Unified entry point (cc-neuron) running both queues
    - config: Worker configuration management

Example:
    Start the unified worker via CLI entry point::

        # Start unified worker (both LLM and Operations queues)
        cc-neuron

    For programmatic access to configuration::

        from chaoscypher_neuron.config import load_worker_config

        config = load_worker_config("llm_worker")
        llm_timeout = config["timeout"]

Note:
    Workers require Valkey connection. Configure via environment:
    - QUEUE_HOST: Valkey hostname (default: valkey)
    - QUEUE_PORT: Valkey port (default: 6379)

See Also:
    - packages/docker/multi-container/docker-compose.dev.yml for service orchestration
    - packages/cortex/src/chaoscypher_cortex/shared/config for timeout/retry settings
"""

__version__ = "0.1.0"

# Export config functions and types for external use
from chaoscypher_neuron.config import load_worker_config
from chaoscypher_neuron.types import WorkerContext


__all__ = ["WorkerContext", "load_worker_config"]

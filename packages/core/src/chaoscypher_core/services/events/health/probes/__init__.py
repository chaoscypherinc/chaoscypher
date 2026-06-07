# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Health probe implementations.

Contains concrete probe classes that check specific aspects of
system health (disk space, database integrity, error rates,
LLM providers, queue, workers, search index, graph, etc.).

Exports:
    CloudProviderProbe: Checks cloud LLM API key configuration.
    DatabaseProbe: Checks SQLite database integrity via quick_check().
    DiskSpaceProbe: Monitors available disk space with configurable thresholds.
    EmbeddingProbe: Checks embedding provider health.
    ErrorRateProbe: Monitors task failure rates over a sliding window.
    GraphProbe: Checks graph database entity/relationship counts.
    ModelProbe: Checks if a specific Ollama model is installed.
    OllamaProbe: Checks Ollama server connectivity and version.
    QueueProbe: Checks Valkey queue connectivity.
    SearchIndexProbe: Checks search index stats and reindex status.
    WorkerProbe: Checks queue worker heartbeat via Valkey.
"""

from chaoscypher_core.services.events.health.probes.cloud_provider import CloudProviderProbe
from chaoscypher_core.services.events.health.probes.database import DatabaseProbe
from chaoscypher_core.services.events.health.probes.disk_space import DiskSpaceProbe
from chaoscypher_core.services.events.health.probes.embedding import EmbeddingProbe
from chaoscypher_core.services.events.health.probes.error_rate import ErrorRateProbe
from chaoscypher_core.services.events.health.probes.graph import GraphProbe
from chaoscypher_core.services.events.health.probes.ollama import ModelProbe, OllamaProbe
from chaoscypher_core.services.events.health.probes.queue import QueueProbe
from chaoscypher_core.services.events.health.probes.search_index import SearchIndexProbe
from chaoscypher_core.services.events.health.probes.worker import WorkerProbe


__all__ = [
    "CloudProviderProbe",
    "DatabaseProbe",
    "DiskSpaceProbe",
    "EmbeddingProbe",
    "ErrorRateProbe",
    "GraphProbe",
    "ModelProbe",
    "OllamaProbe",
    "QueueProbe",
    "SearchIndexProbe",
    "WorkerProbe",
]

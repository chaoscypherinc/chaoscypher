# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Graph Snapshot Feature.

Pre-computed graph breakdown snapshot for dashboard rendering.

This feature exposes a read endpoint to retrieve the latest graph snapshot
(a ``GraphBreakdown`` produced by the Neuron ``build_graph_snapshot`` operation)
and a write endpoint to trigger an on-demand rebuild via the operations queue.

Components:
- GraphSnapshotFeatureService: Thin read path wrapping GraphSnapshotRepository
- GraphBreakdown: Re-export of Core model so OpenAPI picks it up here
- router: FastAPI endpoints for GET /api/v1/graph/snapshot and
  POST /api/v1/graph/snapshot/refresh

Architecture:
VSA slice — service delegates to ``GraphSnapshotRepository`` (engine-based);
the POST handler enqueues ``OP_BUILD_GRAPH_SNAPSHOT`` directly without a
service wrapper (thin 202 pass-through).

Example:
    from chaoscypher_cortex.features.graph_snapshot import router

"""

from chaoscypher_cortex.features.graph_snapshot.api import router
from chaoscypher_cortex.features.graph_snapshot.models import GraphBreakdown
from chaoscypher_cortex.features.graph_snapshot.service import GraphSnapshotFeatureService


__all__ = [
    "GraphBreakdown",
    "GraphSnapshotFeatureService",
    "router",
]

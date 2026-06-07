# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests: GroundingService raises Core domain exceptions, not HTTPException.

Part of Workstream B / Decision 3 of the 2026-04-23 architecture audit.
The service layer must not depend on FastAPI's HTTPException; transport
mapping happens at the Cortex boundary via chaoscypher_exception_handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_cortex.features.graph.grounding_service import GroundingService


def _make_service(node=None) -> GroundingService:
    """Build a GroundingService with a mock graph repository and settings."""
    repo = MagicMock()
    repo.get_node.return_value = node  # default: None = not found
    repo.list_edges.return_value = []
    repo.get_nodes_batch.return_value = []
    settings = MagicMock()
    settings.pagination.default_list_limit = 50
    settings.batching.edge_list_limit = 100
    return GroundingService(graph_repository=repo, settings=settings)


def test_get_node_with_edges_raises_not_found_when_missing() -> None:
    """get_node_with_edges raises NotFoundError (not HTTPException) for missing node."""
    service = _make_service(node=None)
    with pytest.raises(NotFoundError) as exc_info:
        service.get_node_with_edges("nonexistent-node")
    assert exc_info.value.resource_type == "Node"
    assert exc_info.value.identifier == "nonexistent-node"


def test_get_node_neighbors_raises_validation_error_for_invalid_direction() -> None:
    """get_node_neighbors raises ValidationError (not HTTPException) for bad direction."""
    service = _make_service(node=MagicMock())
    with pytest.raises(ValidationError) as exc_info:
        service.get_node_neighbors("any-node", direction="sideways")
    assert "direction" in exc_info.value.message.lower()


def test_get_node_neighbors_raises_not_found_when_node_missing() -> None:
    """get_node_neighbors raises NotFoundError (not HTTPException) for missing node."""
    service = _make_service(node=None)
    with pytest.raises(NotFoundError) as exc_info:
        service.get_node_neighbors("nonexistent-node", direction="both")
    assert exc_info.value.resource_type == "Node"
    assert exc_info.value.identifier == "nonexistent-node"

# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests pinning the /api/v1/search wire shape for node results.

Node entries in search responses must carry the SearchNodeHit projection
({id, label, template_id}) and MUST NOT leak the full node payload
(embedding, properties, position, created_at, updated_at) that the old
NodeResponse surfaced. These tests guard both the DTO shape and the
SearchService conversion path so any future change that re-widens the
projection is caught early.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_cortex.features.search.models import (
    SearchNodeHit,
    SearchResponse,
    SearchResult,
)
from chaoscypher_cortex.features.search.service import SearchService


# Fields the nodes feature's NodeResponse surfaces but which the narrower
# SearchNodeHit projection intentionally drops. Tracked here so new fields
# added to the nodes feature that don't belong in search are automatically
# caught.
REMOVED_FIELDS = frozenset({"embedding", "properties", "position", "created_at", "updated_at"})
# The exact projection SearchNodeHit commits to surfacing.
# ``edge_count`` was added (2026-04) so the omnibar can display real
# connection counts instead of a hardcoded 0; it's part of the narrow
# wire shape and intentionally lives here rather than in REMOVED_FIELDS.
PROJECTION_FIELDS = frozenset({"id", "label", "template_id", "edge_count"})


def _make_service() -> SearchService:
    """Return a SearchService with a mocked engine service.

    Patches EngineSearchService and build_engine_settings so the
    constructor does not require real adapters — we only exercise
    _convert_to_pydantic.
    """
    with (
        patch(
            "chaoscypher_cortex.features.search.service.EngineSearchService",
            return_value=MagicMock(),
        ),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=None,
        ),
    ):
        return SearchService(
            search_repository=MagicMock(),
            graph_repository=MagicMock(),
            indexing_repository=MagicMock(),
            source_repository=MagicMock(),
            sources_repository=MagicMock(),
            settings=None,
        )


@pytest.mark.unit
class TestSearchNodeHitShape:
    """Tests the SearchNodeHit DTO shape invariant."""

    def test_model_dump_keys_exactly_projection_fields(self) -> None:
        """SearchNodeHit.model_dump() keys are exactly {id, label, template_id}."""
        hit = SearchNodeHit(id="n1", label="Alice", template_id="person")

        node_dict = hit.model_dump()

        assert set(node_dict.keys()) == PROJECTION_FIELDS

    def test_projection_fields_carry_correct_values(self) -> None:
        """Serialized dict preserves the id, label, template_id inputs."""
        hit = SearchNodeHit(id="n1", label="Alice", template_id="person")

        node_dict = hit.model_dump()

        assert node_dict["id"] == "n1"
        assert node_dict["label"] == "Alice"
        assert node_dict["template_id"] == "person"

    def test_removed_fields_absent_from_serialized_dict(self) -> None:
        """embedding/properties/position/created_at/updated_at MUST NOT appear."""
        hit = SearchNodeHit(id="n1", label="Alice", template_id="person")

        node_dict = hit.model_dump()

        for removed_field in REMOVED_FIELDS:
            assert removed_field not in node_dict, (
                f"SearchNodeHit leaked '{removed_field}' from the projection"
            )


@pytest.mark.unit
class TestSearchServiceConversionProjection:
    """Tests SearchService._convert_to_pydantic drops leaky engine fields."""

    def test_convert_keeps_only_projection_fields_for_node_result(self) -> None:
        """_convert_to_pydantic strips engine-only fields from node hits.

        The engine may return a full node payload (properties, embedding,
        position, created_at, updated_at). The service MUST project it down
        to SearchNodeHit so the wire shape stays narrow.
        """
        service = _make_service()
        engine_response = {
            "type": "semantic",
            "data": [
                {
                    "result_type": "node",
                    "score": 0.95,
                    "node": {
                        "id": "n1",
                        "template_id": "person",
                        "label": "Alice",
                        # Engine-only fields that MUST NOT surface in the response:
                        "properties": {"age": 30},
                        "embedding": [0.1, 0.2, 0.3],
                        "position": {"x": 0, "y": 0},
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    },
                }
            ],
        }

        response = service._convert_to_pydantic(engine_response)

        assert isinstance(response, SearchResponse)
        assert response.type == "semantic"
        assert len(response.data) == 1
        result = response.data[0]
        assert isinstance(result, SearchResult)
        assert result.result_type == "node"
        assert result.node is not None

        node_dict = result.node.model_dump()
        assert set(node_dict.keys()) == PROJECTION_FIELDS
        assert node_dict["id"] == "n1"
        assert node_dict["label"] == "Alice"
        assert node_dict["template_id"] == "person"
        for removed_field in REMOVED_FIELDS:
            assert removed_field not in node_dict, (
                f"_convert_to_pydantic leaked '{removed_field}' into the wire shape"
            )

    def test_convert_handles_node_without_template_id(self) -> None:
        """Nodes without a template still produce valid SearchNodeHit output."""
        service = _make_service()
        engine_response = {
            "type": "keyword",
            "data": [
                {
                    "result_type": "node",
                    "score": 0.5,
                    "node": {
                        "id": "n2",
                        "label": "Unclassified",
                        # No template_id, but engine still leaks other fields:
                        "properties": {"note": "orphan"},
                        "embedding": [0.9],
                    },
                }
            ],
        }

        response = service._convert_to_pydantic(engine_response)

        assert response.data[0].node is not None
        node_dict = response.data[0].node.model_dump()
        assert set(node_dict.keys()) == PROJECTION_FIELDS
        assert node_dict["template_id"] is None
        for removed_field in REMOVED_FIELDS:
            assert removed_field not in node_dict

    def test_serialized_wire_response_has_narrow_node_shape(self) -> None:
        """End-to-end: the JSON-serializable response carries only projection fields.

        This is what the HTTP client actually sees on /api/v1/search.
        """
        service = _make_service()
        engine_response = {
            "type": "hybrid",
            "data": [
                {
                    "result_type": "node",
                    "score": 0.7,
                    "node": {
                        "id": "n3",
                        "template_id": "document",
                        "label": "Report",
                        "properties": {"size": 1024},
                        "embedding": [0.5, 0.6],
                        "position": {"x": 10, "y": 20},
                        "created_at": "2026-02-01T00:00:00Z",
                        "updated_at": "2026-02-02T00:00:00Z",
                    },
                }
            ],
        }

        response = service._convert_to_pydantic(engine_response)

        # model_dump() on the full response mirrors what FastAPI serializes.
        wire_payload = response.model_dump()
        node_dict = wire_payload["data"][0]["node"]

        assert set(node_dict.keys()) == PROJECTION_FIELDS
        for removed_field in REMOVED_FIELDS:
            assert removed_field not in node_dict, (
                f"Wire payload leaked '{removed_field}' — search API contract broken"
            )

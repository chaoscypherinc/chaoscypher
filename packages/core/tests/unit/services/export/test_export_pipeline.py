# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for export pipeline embedding threading."""

from chaoscypher_core.services.export.management.metadata_manager import (
    serialize_and_checksum,
)


class TestSerializeAndChecksumEmbeddings:
    """Tests for embedding stripping in serialize_and_checksum."""

    def _make_separated_data(self) -> dict:
        return {
            "templates": [],
            "knowledge_nodes": [
                {
                    "id": "node_1",
                    "label": "Alice",
                    "template_id": "person",
                    "embedding": [0.1, 0.2, 0.3],
                    "properties": {"name": "Alice"},
                },
                {
                    "id": "node_2",
                    "label": "Bob",
                    "template_id": "person",
                    "embedding": None,
                    "properties": {"name": "Bob"},
                },
            ],
            "knowledge_edges": [],
            "lens_nodes": [],
            "lens_edges": [],
            "workflow_nodes": [],
            "workflow_edges": [],
            "triggers": [],
            "sources": [],
        }

    def test_knowledge_node_embeddings_stripped_by_default(self):
        """Knowledge node embeddings stripped when include_embeddings=False."""
        data = self._make_separated_data()
        result = serialize_and_checksum(data, include_embeddings=False)

        import json

        knowledge = json.loads(result["knowledge"]["json"])
        for node in knowledge["nodes"]:
            assert "embedding" not in node

    def test_knowledge_node_embeddings_kept_when_included(self):
        """Knowledge node embeddings kept when include_embeddings=True."""
        data = self._make_separated_data()
        result = serialize_and_checksum(data, include_embeddings=True)

        import json

        knowledge = json.loads(result["knowledge"]["json"])
        assert knowledge["nodes"][0]["embedding"] == [0.1, 0.2, 0.3]

    def test_knowledge_node_other_fields_preserved(self):
        """Non-embedding fields preserved when embeddings stripped."""
        data = self._make_separated_data()
        result = serialize_and_checksum(data, include_embeddings=False)

        import json

        knowledge = json.loads(result["knowledge"]["json"])
        assert knowledge["nodes"][0]["label"] == "Alice"
        assert knowledge["nodes"][0]["properties"] == {"name": "Alice"}

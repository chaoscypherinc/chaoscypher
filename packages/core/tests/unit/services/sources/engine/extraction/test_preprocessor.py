# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for extraction preprocessor entity normalization."""

from __future__ import annotations

import pytest

from chaoscypher_core.services.sources.engine.extraction.preprocessor import (
    normalize_entities,
)


# ---------------------------------------------------------------------------
# TestNormalizeEntities
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeEntities:
    """Tests for normalize_entities()."""

    def test_empty_list_returns_empty(self) -> None:
        """Empty input yields an empty output list."""
        assert normalize_entities([]) == []

    def test_applies_defaults_for_missing_fields(self) -> None:
        """Missing optional fields receive sensible defaults."""
        result = normalize_entities([{}])
        assert len(result) == 1
        entity = result[0]
        assert entity["id"] == "entity_0"
        assert entity["name"] == "Unknown"
        assert entity["type"] == "Unknown"
        assert entity["description"] == ""
        assert entity["properties"] == {}
        assert entity["aliases"] == []
        assert entity["confidence"] == 1.0
        assert entity["chunk_index"] is None
        assert entity["sent_ref"] is None
        assert entity["source_chunk_indices"] is None

    def test_preserves_explicit_values(self) -> None:
        """Explicit entity fields are preserved unchanged."""
        raw = [
            {
                "id": "e1",
                "name": "Alice",
                "type": "Person",
                "description": "Protagonist",
                "properties": {"age": 30},
                "aliases": ["A"],
                "confidence": 0.85,
                "chunk_index": 3,
                "sent_ref": "s_42",
            }
        ]
        result = normalize_entities(raw)
        entity = result[0]
        assert entity["id"] == "e1"
        assert entity["name"] == "Alice"
        assert entity["type"] == "Person"
        assert entity["description"] == "Protagonist"
        assert entity["properties"] == {"age": 30}
        assert entity["aliases"] == ["A"]
        assert entity["confidence"] == 0.85
        assert entity["chunk_index"] == 3
        assert entity["sent_ref"] == "s_42"

    def test_generates_sequential_default_ids(self) -> None:
        """Entities without ids get `entity_{idx}` defaults in order."""
        result = normalize_entities([{"name": "A"}, {"name": "B"}, {"name": "C"}])
        ids = [e["id"] for e in result]
        assert ids == ["entity_0", "entity_1", "entity_2"]

    def test_source_chunk_indices_derived_from_chunk_index(self) -> None:
        """source_chunk_indices falls back to [chunk_index] when present."""
        result = normalize_entities([{"name": "A", "chunk_index": 5}])
        assert result[0]["source_chunk_indices"] == [5]

    def test_explicit_source_chunk_indices_wins(self) -> None:
        """An explicit source_chunk_indices overrides the chunk_index fallback."""
        result = normalize_entities(
            [{"name": "A", "chunk_index": 5, "source_chunk_indices": [1, 2, 3]}]
        )
        assert result[0]["source_chunk_indices"] == [1, 2, 3]

    def test_rejected_aliases_included_when_present(self) -> None:
        """rejected_aliases field is carried through only when truthy."""
        with_rejected = normalize_entities([{"name": "A", "rejected_aliases": ["bad1"]}])
        assert with_rejected[0]["rejected_aliases"] == ["bad1"]

    def test_rejected_aliases_skipped_when_empty(self) -> None:
        """rejected_aliases key is omitted when empty or missing."""
        without = normalize_entities([{"name": "A"}])
        assert "rejected_aliases" not in without[0]

        empty = normalize_entities([{"name": "A", "rejected_aliases": []}])
        assert "rejected_aliases" not in empty[0]

    def test_multiple_entities_normalized_independently(self) -> None:
        """Each entity in the list is normalized independently."""
        raw = [
            {"id": "x", "name": "Alice", "type": "Person"},
            {"name": "Beta"},
        ]
        result = normalize_entities(raw)
        assert len(result) == 2
        assert result[0]["id"] == "x"
        assert result[0]["type"] == "Person"
        assert result[1]["id"] == "entity_1"
        assert result[1]["type"] == "Unknown"

    def test_chunk_index_zero_still_produces_indices(self) -> None:
        """chunk_index=0 is a valid value and yields [0] for source_chunk_indices."""
        result = normalize_entities([{"name": "A", "chunk_index": 0}])
        assert result[0]["source_chunk_indices"] == [0]

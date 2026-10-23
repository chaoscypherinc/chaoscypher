# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Cortex/CLI/MCP finalizer must apply structural-entity filter and type normalizer.

Workstream 3, Tasks 3.1+3.2: the production extraction path
(``ExtractionService.finalize_distributed_extraction``) historically skipped
``filter_structural_entities`` and ``normalize_entity_types``. Only the
standalone CLI helper ``extract_entities_from_groups`` ran them, so the same
source uploaded through Cortex vs CLI produced different graphs.

These tests assert the production finalizer has parity with the standalone
helper: structural entities never reach the committed graph, and generic
``Thing``-typed (generic) entities with rule-matching descriptions get re-typed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.sources.engine.extraction.service import (
    ExtractionService,
)


def _fake_settings() -> SimpleNamespace:
    """Build minimal settings sufficient for finalize_distributed_extraction."""
    return SimpleNamespace(
        source_processing=SimpleNamespace(
            entity_max_description_length=4000,
            entity_deduplication_mode="exact",
            dedup_require_type_compatibility=False,
        ),
        extraction=SimpleNamespace(
            semantic_dedup_threshold=0.95,
            extraction_filtering_mode="standard",
        ),
        embedding=SimpleNamespace(model="test-embed-model"),
    )


def _make_service() -> ExtractionService:
    """ExtractionService with mocked external dependencies."""
    return ExtractionService(
        graph_repository=MagicMock(name="graph_repository"),
        llm_provider=MagicMock(name="llm_provider"),
        settings=_fake_settings(),
        embedding_service=None,
    )


def _passthrough_run_dedup(
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> Any:
    """Build an async run_deduplication patch that passes inputs through unchanged."""

    async def _impl(
        *,
        entities: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        **_: object,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any], dict[str, Any]]:
        # Echo the deduplicated input back so the test can isolate the
        # structural-filter and type-normalizer effects.
        return entities, relationships, [], {}

    return _impl


@pytest.mark.unit
@pytest.mark.asyncio
async def test_finalizer_filters_structural_entities() -> None:
    """A 'STRUCTURAL_UNIT' entity must not survive into the committed graph."""
    service = _make_service()
    raw_entities = [
        {"name": "Napoleon", "type": "Person"},
        {"name": "Chapter 5", "type": "STRUCTURAL_UNIT"},
        {"name": "Moscow", "type": "Place"},
    ]
    raw_relationships = [
        {"source": 0, "target": 2, "type": "occupied"},
        {"source": 0, "target": 1, "type": "narrated_in"},  # to structural unit
    ]

    with patch(
        "chaoscypher_core.services.sources.engine.extraction.service.run_deduplication",
        side_effect=_passthrough_run_dedup(raw_entities, raw_relationships),
    ):
        result = await service.finalize_distributed_extraction(
            raw_entities=raw_entities,
            raw_relationships=raw_relationships,
            generate_embeddings=False,
            detected_domain="literary",
        )

    surviving_names = {e["name"] for e in result["entities"]}
    assert "Chapter 5" not in surviving_names, "structural entity should be filtered before commit"
    assert "Napoleon" in surviving_names
    assert "Moscow" in surviving_names

    # Relationship that pointed at the filtered entity should also be gone.
    rel_types = {r["type"] for r in result["relationships"]}
    assert "narrated_in" not in rel_types, (
        "relationship into removed structural entity should be dropped"
    )
    assert "occupied" in rel_types


@pytest.mark.unit
@pytest.mark.asyncio
async def test_finalizer_normalizes_generic_entity_types() -> None:
    """An entity typed 'Thing' (generic, no core type) with description matching domain rules gets re-typed."""
    service = _make_service()
    raw_entities = [
        {
            "name": "Mailbox",
            "type": "Thing",
            "description": "A report on the inbox module that handles message routing.",
        },
    ]
    raw_relationships: list[dict[str, Any]] = []

    # Wire a fake domain that exposes normalization rules mapping the
    # phrase "a report" -> the core type "Work".
    fake_domain = MagicMock()
    fake_domain.role = "pack"
    fake_domain.name = "technical"
    fake_domain.config = {}
    fake_domain.get_normalization_rules.return_value = {
        "Work": ["a report", "report that"],
    }
    fake_domain.get_title_words.return_value = []
    fake_domain.get_symmetric_relationships.return_value = []
    fake_domain.get_inverse_relationships.return_value = {}
    fake_domain.get_templates.return_value = {"node_templates": [], "edge_templates": []}
    fake_domain.subtypes.return_value = []
    fake_domain.get_type_aliases.return_value = {}
    fake_domain.core_rescue_keywords.return_value = {}

    # The finalize reads the source's stack (Task 7): the real floor plus the fake pack.
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry

    floor = get_domain_registry().get_domain("generic")
    fake_registry = MagicMock()
    fake_registry.get_domain.side_effect = {"generic": floor, "technical": fake_domain}.get

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.domains.get_domain_registry",
            return_value=fake_registry,
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.service.run_deduplication",
            side_effect=_passthrough_run_dedup(raw_entities, raw_relationships),
        ),
    ):
        result = await service.finalize_distributed_extraction(
            raw_entities=raw_entities,
            raw_relationships=raw_relationships,
            generate_embeddings=False,
            detected_domain="technical",
        )

    mailbox = next(e for e in result["entities"] if e["name"] == "Mailbox")
    assert mailbox["type"] == "Work", (
        f"expected 'Work' after normalization, got {mailbox['type']!r}"
    )
    assert mailbox.get("type_normalized_from") == "Thing"

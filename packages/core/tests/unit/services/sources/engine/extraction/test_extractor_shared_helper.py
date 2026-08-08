# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``extract_entities_from_groups`` must use the shared post-extraction helper.

CANONICAL_PATTERNS § Post-extraction filters: all three finalize sites
(standalone ``extract_entities_from_groups``, the service
``finalize_distributed_extraction``, the worker
``_finalize_extraction_inner``) run ``apply_structural_and_normalization``
AFTER dedup. The standalone helper historically ran
``filter_structural_entities`` PRE-dedup and ``normalize_entity_types``
separately, so a structural entity that only materialises after dedup
merging (the canonical form of merged name variants) survived on the
standalone path but was filtered on the other two.

These tests pin the standalone path to the canonical ordering and
semantics.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.sources.engine.extraction.extractor import (
    extract_entities_from_groups,
)


def _fake_settings() -> SimpleNamespace:
    """Minimal settings for the standalone extraction path."""
    return SimpleNamespace(
        source_processing=SimpleNamespace(
            entity_max_description_length=4000,
            entity_deduplication_mode="exact",
            dedup_require_type_compatibility=False,
            dedup_type_compatibility_map={},
        ),
        extraction=SimpleNamespace(
            semantic_dedup_threshold=0.95,
            extraction_filtering_mode="balanced",
            dedup_type_partition_cutoff=200,
            dedup_no_overlap_boost=0.0,
            dedup_borderline_penalty=0.0,
        ),
        embedding=SimpleNamespace(model="test-embed-model"),
    )


def _groups() -> list[dict[str, Any]]:
    return [{"combined_content": "some text", "small_chunk_ids": ["chunk-1"]}]


def _fake_extractor(extraction_result: dict[str, Any]) -> MagicMock:
    """AIEntityExtractor stand-in returning a fixed extraction result."""
    instance = MagicMock()

    async def _extract(*_: object, **__: object) -> dict[str, Any]:
        return extraction_result

    instance.extract_from_chunks = _extract
    return MagicMock(return_value=instance)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_structural_entity_emerging_from_dedup_is_filtered() -> None:
    """A structural entity that only exists AFTER dedup must still be filtered.

    Ordering fixture: dedup merging name variants can produce a canonical
    entity with a structural type that was not present pre-dedup. The
    pre-dedup filter (old behavior) never sees it; the canonical
    post-dedup helper does. Simulated by a run_deduplication stub that
    appends the structural entity — exactly what a merge produces.
    """
    extraction_result = {
        "entities": [{"name": "Napoleon", "type": "Person"}],
        "relationships": [],
        "domain": "generic",
        "domain_confidence": 0.5,
        "normalization_rules": {},
    }

    async def _dedup_appends_structural(
        *, entities: list[dict[str, Any]], relationships: list[dict[str, Any]], **_: object
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any], dict[str, Any]]:
        merged = [*entities, {"name": "Chapter 5", "type": "STRUCTURAL_UNIT"}]
        return merged, relationships, [], {}

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.extractor.AIEntityExtractor",
            _fake_extractor(extraction_result),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.extractor.run_deduplication",
            side_effect=_dedup_appends_structural,
        ),
    ):
        result = await extract_entities_from_groups(
            hierarchical_groups=_groups(),
            settings=_fake_settings(),
            embedding_service=None,
        )

    surviving = {e["name"] for e in result["entities"]}
    assert "Chapter 5" not in surviving, (
        "structural entity produced by dedup merging must be filtered — the "
        "structural filter has to run POST-dedup via the shared helper"
    )
    assert "Napoleon" in surviving


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shared_helper_invoked_after_dedup() -> None:
    """The shared helper runs exactly once, after run_deduplication."""
    call_order: list[str] = []

    extraction_result = {
        "entities": [{"name": "Napoleon", "type": "Person"}],
        "relationships": [],
        "domain": "generic",
        "domain_confidence": 0.5,
        "normalization_rules": {},
    }

    async def _spy_dedup(
        *, entities: list[dict[str, Any]], relationships: list[dict[str, Any]], **_: object
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any], dict[str, Any]]:
        call_order.append("run_deduplication")
        return entities, relationships, [], {}

    def _spy_helper(
        entities: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        **_: object,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        call_order.append("apply_structural_and_normalization")
        return entities, relationships, 0

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.extractor.AIEntityExtractor",
            _fake_extractor(extraction_result),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.extractor.run_deduplication",
            side_effect=_spy_dedup,
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.extractor.apply_structural_and_normalization",
            side_effect=_spy_helper,
        ),
    ):
        await extract_entities_from_groups(
            hierarchical_groups=_groups(),
            settings=_fake_settings(),
            embedding_service=None,
        )

    assert call_order == ["run_deduplication", "apply_structural_and_normalization"], (
        f"expected the shared helper to run once, after dedup; got {call_order}"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_normalization_rules_still_applied_post_dedup() -> None:
    """Generic 'Item' entities are re-typed via the extraction's rules.

    Semantics-preservation pin: routing through the shared helper must
    keep the standalone path's normalization outcome identical to the
    canonical path (rule-matching description re-types the entity and
    records ``type_normalized_from``).
    """
    extraction_result = {
        "entities": [
            {
                "name": "Mailbox",
                "type": "Item",
                "description": "A class in the inbox module that handles message routing.",
            }
        ],
        "relationships": [],
        "domain": "technical",
        "domain_confidence": 0.9,
        "normalization_rules": {"Class": ["a class", "class that"]},
    }

    async def _passthrough_dedup(
        *, entities: list[dict[str, Any]], relationships: list[dict[str, Any]], **_: object
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any], dict[str, Any]]:
        return entities, relationships, [], {}

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.extractor.AIEntityExtractor",
            _fake_extractor(extraction_result),
        ),
        patch(
            "chaoscypher_core.services.sources.engine.extraction.extractor.run_deduplication",
            side_effect=_passthrough_dedup,
        ),
    ):
        result = await extract_entities_from_groups(
            hierarchical_groups=_groups(),
            settings=_fake_settings(),
            embedding_service=None,
        )

    mailbox = next(e for e in result["entities"] if e["name"] == "Mailbox")
    assert mailbox["type"] == "Class"
    assert mailbox.get("type_normalized_from") == "Item"

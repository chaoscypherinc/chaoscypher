# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pipeline must dedup entities before dropping relationships.

Regression: in the war_and_peace phi4 import, 'Princess Anna Mikháylovna
Drubetskáya' (the long form) ended with 0 edges and 'Princess Drubetskáya'
(the short form) had 87. The long form's only relationship was killed
by the type-constraint filter before dedup got a chance to merge it.

The fix: run dedup BEFORE the relationship-dropping filters. Dedup remaps
relationship indices to canonical entities, so the filters see consolidated
edges on well-typed canonical entities and stop killing minor variants'
only edge.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.sources.engine.extraction.extractor import (
    extract_entities_from_groups,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    FilteringConfig,
)
from chaoscypher_core.settings import EngineSettings


# --------------------------------------------------------------------- #
#  Test fixtures and helpers
# --------------------------------------------------------------------- #


@pytest.fixture
def minimal_extraction_settings() -> EngineSettings:
    """A default EngineSettings — extraction defaults are fine for these tests."""
    return EngineSettings()


def _fake_extractor(extraction_result: dict[str, Any]) -> MagicMock:
    """AIEntityExtractor stand-in returning a fixed extraction result."""
    instance = MagicMock()

    async def _extract(*_: object, **__: object) -> dict[str, Any]:
        return extraction_result

    instance.extract_from_chunks = _extract
    return MagicMock(return_value=instance)


async def run_pipeline(
    *,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    settings: EngineSettings,
    edge_type_constraints: dict[str, dict[str, list[str]]] | None = None,
    filtering_config: FilteringConfig | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drive the REAL production pipeline: ``extract_entities_from_groups``.

    The ordering under test (dedup BEFORE the relationship-dropping filters)
    lives in ``extract_entities_from_groups``, so this helper must not
    re-implement it — a helper that called ``run_deduplication`` and then
    ``apply_cross_chunk_relationship_filters`` itself would stay green with
    the pre-Phase-6 order restored in production. Only the LLM call is
    stubbed; every step after it is the production code path. Tests drive
    this rather than poking at per-chunk filters because per-chunk filters
    are intentionally narrowed to evidence-only (which depends on chunk-local
    sentences) post-Phase 6.

    Args:
        entities: Raw extracted entities (as if emitted by the LLM).
        relationships: Raw extracted relationships.
        settings: EngineSettings instance.
        edge_type_constraints: Optional edge-type constraints dict.
        filtering_config: Optional FilteringConfig (defaults to balanced).

    Returns:
        Tuple of (final_entities, final_relationships).
    """
    extraction_result: dict[str, Any] = {
        "entities": entities,
        "relationships": relationships,
        "domain": "generic",
        "domain_confidence": 0.0,
        "normalization_rules": {},
        "edge_type_constraints": edge_type_constraints,
    }
    cfg = filtering_config or FilteringConfig()

    with (
        patch(
            "chaoscypher_core.services.sources.engine.extraction.extractor.AIEntityExtractor",
            _fake_extractor(extraction_result),
        ),
        # resolve_filtering_config is imported inside the production function,
        # so patch it at its definition site to inject the test's config.
        patch(
            "chaoscypher_core.services.sources.engine.extraction.utils.filtering_config.resolve_filtering_config",
            return_value=cfg,
        ),
    ):
        result = await extract_entities_from_groups(
            hierarchical_groups=[
                {"combined_content": "war and peace excerpt", "small_chunk_ids": ["chunk-1"]}
            ],
            settings=settings,
            # exact-name dedup is enough for these tests
            embedding_service=None,
        )

    return result["entities"], result["relationships"]


# --------------------------------------------------------------------- #
#  Tests
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_long_name_variant_merges_with_short_form_before_filters(
    minimal_extraction_settings: EngineSettings,
) -> None:
    """Two extracted entities with the same canonical name converge before filters run.

    Setup: emit a long-form entity with one weakly-typed relationship that
    today's filter would drop, and a short-form entity with strong relationships.
    After the pipeline, only ONE Drubetskáya entity survives, carrying ALL
    surviving relationships from both variants.
    """
    # Both 'Princess Anna Mikháylovna Drubetskáya' and 'Princess Drubetskáya'
    # collapse via exact-name dedup once names are normalized — but the long
    # form here is a strict superstring sharing word roots. To make the
    # exact-name dedup match, use the same canonical name. The realistic
    # case in extraction is the LLM emitting both forms with the same
    # ``name`` field but different ``aliases``; the entity_processor's
    # exact-name pass collapses them by name + type.
    entities = [
        # Long-form ends up under canonical name 'Princess Drubetskáya' after
        # the entity_processor's name-normalization step. We simulate that
        # by giving both entities the same lowercased name so the exact-name
        # dedup pass collapses them. The downstream remapping then routes
        # the long-form's relationships through the canonical entity.
        {
            "name": "Princess Drubetskáya",
            "type": "Person",
            "aliases": ["Princess Anna Mikháylovna Drubetskáya"],
        },
        {"name": "Princess Drubetskáya", "type": "Person"},
        {"name": "Boris Drubetskoy", "type": "Person"},
    ]
    relationships = [
        # Long-form's only edge — would be dropped by type filter today
        # because 'weakly_typed_relationship' is not in the edge_type_constraints
        # (strict mode drops it).
        {
            "source": 0,
            "target": 2,
            "type": "weakly_typed_relationship",
            "confidence": 0.5,
            "chunk_index": 0,
        },
        # Short-form has strong, well-typed ties.
        {"source": 1, "target": 2, "type": "parent_of", "confidence": 0.95, "chunk_index": 0},
        {"source": 1, "target": 2, "type": "confides_in", "confidence": 0.9, "chunk_index": 1},
    ]

    edge_type_constraints = {
        "parent_of": {"source_types": ["Person"], "target_types": ["Person"]},
        "confides_in": {"source_types": ["Person"], "target_types": ["Person"]},
        # 'weakly_typed_relationship' deliberately absent.
    }

    # Strict mode drops unmatched types — that's the filter that today
    # kills the long-form's only edge.
    cfg = FilteringConfig(
        enable_type_constraints=True,
        strict_edge_type_constraints=True,
        # Disable evidence filter (depends on per-chunk sentences which
        # this synthetic test doesn't supply).
        evidence_validation_mode="off",
        enable_relationship_limits=True,
    )

    result_entities, result_rels = await run_pipeline(
        entities=entities,
        relationships=relationships,
        settings=minimal_extraction_settings,
        edge_type_constraints=edge_type_constraints,
        filtering_config=cfg,
    )

    # Only the Princess (not Boris) — exclude Drubetskoy variants.
    drubetskaya = [e for e in result_entities if e["name"].startswith("Princess")]
    assert len(drubetskaya) == 1, (
        f"expected one merged Princess Drubetskáya, got {[e['name'] for e in drubetskaya]}"
    )

    canonical_idx = result_entities.index(drubetskaya[0])
    drubetskaya_rels = [
        r for r in result_rels if r["source"] == canonical_idx or r["target"] == canonical_idx
    ]
    # The merged canonical entity should keep at least the two well-typed
    # edges (parent_of + confides_in). The 'weakly_typed_relationship' edge
    # may or may not survive — what matters is the canonical entity isn't
    # left orphaned, and the two strong edges still anchor on it.
    surviving_types = {r["type"] for r in drubetskaya_rels}
    assert "parent_of" in surviving_types, (
        f"merged entity should keep parent_of, got types {surviving_types}"
    )
    assert "confides_in" in surviving_types, (
        f"merged entity should keep confides_in, got types {surviving_types}"
    )
    assert len(drubetskaya_rels) >= 2, (
        f"merged entity should keep parent_of + confides_in, got {drubetskaya_rels}"
    )


@pytest.mark.asyncio
async def test_dedup_runs_before_filters(
    minimal_extraction_settings: EngineSettings,
) -> None:
    """Exact-name dedup remaps relationship indices before filters see them.

    Sanity check that the pipeline isn't accidentally restored to old
    order: if a duplicate-name pair is fed in, the filter pass should see
    one entity, not two.
    """
    entities = [
        {"name": "Pierre Bezúkhov", "type": "Person"},
        {"name": "Pierre Bezúkhov", "type": "Person"},  # duplicate
        {"name": "Natásha Rostóva", "type": "Person"},
    ]
    relationships = [
        {"source": 0, "target": 2, "type": "marries", "confidence": 0.9, "chunk_index": 0},
        {"source": 1, "target": 2, "type": "marries", "confidence": 0.9, "chunk_index": 1},
    ]
    cfg = FilteringConfig(
        enable_type_constraints=False,
        evidence_validation_mode="off",
        enable_relationship_limits=True,
    )
    result_entities, result_rels = await run_pipeline(
        entities=entities,
        relationships=relationships,
        settings=minimal_extraction_settings,
        edge_type_constraints=None,
        filtering_config=cfg,
    )

    pierres = [e for e in result_entities if "Pierre" in e["name"]]
    assert len(pierres) == 1, "duplicate Pierre should have been collapsed by dedup"
    # Both relationships were remapped to the canonical Pierre and the
    # filter pass saw them as edges on that single canonical entity.
    pierre_idx = result_entities.index(pierres[0])
    pierre_rels = [r for r in result_rels if r["source"] == pierre_idx or r["target"] == pierre_idx]
    # At minimum: the filter sees consolidated edges on a single entity.
    # (Same-chunk relationship dedup may collapse exact duplicates;
    # cross-chunk ones with different chunk_index survive separately.)
    assert len(pierre_rels) >= 1, (
        f"canonical Pierre should keep at least one edge, got {pierre_rels}"
    )


@pytest.mark.asyncio
async def test_filters_receive_dedup_output_not_raw_extraction(
    minimal_extraction_settings: EngineSettings,
) -> None:
    """``extract_entities_from_groups`` calls dedup, THEN the filters, on its output.

    The behavioural tests above cannot see the ordering directly — a
    fixture's surviving edges can look the same under either order. This
    pins the call site itself, the way the service path is pinned in
    test_extraction_service.py: spy on both production functions, assert
    the order, and assert by IDENTITY that the filters were handed the
    dedup output. Restoring the pre-Phase-6 order (filters on the raw
    extraction, before dedup) fails both assertions.
    """
    call_order: list[str] = []
    dedup_entities = [{"name": "Princess Drubetskáya", "type": "Person"}]
    dedup_rels = [{"source": 0, "target": 0, "type": "parent_of", "confidence": 0.9}]
    seen: dict[str, Any] = {}

    async def _spy_dedup(**_: object) -> tuple[Any, Any, list[Any], dict[str, Any]]:
        call_order.append("run_deduplication")
        return dedup_entities, dedup_rels, [], {}

    def _spy_filters(**kwargs: Any) -> tuple[Any, Any]:
        call_order.append("apply_cross_chunk_relationship_filters")
        seen["entities"] = kwargs["entities"]
        seen["relationships"] = kwargs["relationships"]
        return kwargs["entities"], kwargs["relationships"]

    extraction_result: dict[str, Any] = {
        "entities": [{"name": "Princess Anna Mikháylovna Drubetskáya", "type": "Person"}],
        "relationships": [],
        "domain": "generic",
        "domain_confidence": 0.0,
        "normalization_rules": {},
        "edge_type_constraints": None,
    }

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
            "chaoscypher_core.services.sources.engine.extraction.extractor.apply_cross_chunk_relationship_filters",
            side_effect=_spy_filters,
        ),
    ):
        await extract_entities_from_groups(
            hierarchical_groups=[
                {"combined_content": "war and peace excerpt", "small_chunk_ids": ["chunk-1"]}
            ],
            settings=minimal_extraction_settings,
            embedding_service=None,
        )

    assert call_order == [
        "run_deduplication",
        "apply_cross_chunk_relationship_filters",
    ], f"dedup must run before the relationship-dropping filters; got {call_order}"
    assert seen["entities"] is dedup_entities, (
        "the filters must see the DEDUPLICATED entities — they were handed "
        f"{seen['entities']} instead, i.e. the pre-Phase-6 order is back"
    )
    assert seen["relationships"] is dedup_rels, (
        "the filters must see the REMAPPED relationships — they were handed "
        f"{seen['relationships']} instead, i.e. the pre-Phase-6 order is back"
    )

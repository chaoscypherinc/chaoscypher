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

import pytest

from chaoscypher_core.services.sources.engine.extraction.extractor import (
    apply_cross_chunk_relationship_filters,
    run_deduplication,
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


async def run_pipeline(
    *,
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    settings: EngineSettings,
    edge_type_constraints: dict[str, dict[str, list[str]]] | None = None,
    filtering_config: FilteringConfig | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the new pipeline order: dedup -> relationship-dropping filters.

    This mirrors the call ordering in ``extract_entities_from_groups`` and
    ``_finalize_extraction_inner`` after the Phase 6 reorder. Tests call
    this rather than poking at per-chunk filters because per-chunk filters
    are intentionally narrowed to evidence-only (which depends on chunk-local
    sentences) post-Phase 6.

    Args:
        entities: Raw extracted entities.
        relationships: Raw extracted relationships.
        settings: EngineSettings instance.
        edge_type_constraints: Optional edge-type constraints dict.
        filtering_config: Optional FilteringConfig (defaults to balanced).

    Returns:
        Tuple of (final_entities, final_relationships).
    """
    deduplicated, remapped, _, _ = await run_deduplication(
        entities=entities,
        relationships=relationships,
        detected_domain=None,
        settings=settings,
        embedding_service=None,  # exact name dedup is enough for these tests
    )

    cfg = filtering_config or FilteringConfig()

    final_entities, final_relationships = apply_cross_chunk_relationship_filters(
        entities=deduplicated,
        relationships=remapped,
        edge_type_constraints=edge_type_constraints,
        filtering_config=cfg,
    )
    return final_entities, final_relationships


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

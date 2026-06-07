# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Integration tests for BuildGraphSnapshotService.

All tests use a file-backed SqliteAdapter (via integration_adapter fixture)
and the seed_two_sources_three_templates helper for deterministic data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.services.graph.snapshot.build_service import BuildGraphSnapshotService

from .....fixtures.seed_graph import seed_two_sources_three_templates


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


# ---------------------------------------------------------------------------
# Test 1: full database -- all sources returned
# ---------------------------------------------------------------------------


def test_build_full_db_returns_all_sources(integration_adapter: SqliteAdapter) -> None:
    seed = seed_two_sources_three_templates(integration_adapter)
    svc = BuildGraphSnapshotService.from_adapter(integration_adapter)
    result = svc.build("default")

    assert result.database_name == "default"
    assert result.stats.total_nodes == seed.total_nodes
    assert result.stats.total_edges == seed.total_edges
    assert result.stats.total_sources == 2
    assert len(result.sources) == 2

    # Source A should appear (8 entities > 6 entities -> comes first)
    src_a = next(s for s in result.sources if s.id == "src_a")
    assert src_a.name == "Paper A"
    assert src_a.source_type == "pdf"
    assert src_a.total_entities == seed.src_a_entities

    # TemplateEntry.color falls back to "#888888" for NULL color
    # tpl_concept is used by Source B -- check it there
    src_b = next(s for s in result.sources if s.id == "src_b")
    concept_entry = next(t for t in src_b.templates if t.id == "tpl_concept")
    assert concept_entry.color == "#888888", "NULL template color must fall back to '#888888'"


# ---------------------------------------------------------------------------
# Test 2: single source filter
# ---------------------------------------------------------------------------


def test_build_single_source_filter(integration_adapter: SqliteAdapter) -> None:
    seed = seed_two_sources_three_templates(integration_adapter)
    svc = BuildGraphSnapshotService.from_adapter(integration_adapter)
    result = svc.build("default", source_ids=["src_a"])

    assert len(result.sources) == 1
    assert result.sources[0].id == "src_a"
    assert result.stats.total_sources == 1
    assert result.stats.total_nodes == seed.src_a_entities

    # Cross-source edge must NOT be counted in total_edges
    # Only the 3 internal-A edges count
    assert result.stats.total_edges == seed.src_a_internal_links


# ---------------------------------------------------------------------------
# Test 3: sort order -- Source A (8 entities) before Source B (6 entities)
# ---------------------------------------------------------------------------


def test_build_sorts_sources_by_entity_count(integration_adapter: SqliteAdapter) -> None:
    seed_two_sources_three_templates(integration_adapter)
    svc = BuildGraphSnapshotService.from_adapter(integration_adapter)
    result = svc.build("default")

    assert len(result.sources) == 2
    assert result.sources[0].id == "src_a", "Source A (8 entities) must sort first"
    assert result.sources[1].id == "src_b", "Source B (6 entities) must sort second"


# ---------------------------------------------------------------------------
# Test 4: internal link counts
# ---------------------------------------------------------------------------


def test_build_counts_internal_links_correctly(integration_adapter: SqliteAdapter) -> None:
    seed = seed_two_sources_three_templates(integration_adapter)
    svc = BuildGraphSnapshotService.from_adapter(integration_adapter)
    result = svc.build("default")

    src_a = next(s for s in result.sources if s.id == "src_a")
    src_b = next(s for s in result.sources if s.id == "src_b")

    assert src_a.total_internal_links == seed.src_a_internal_links, (
        "Source A must have exactly 3 internal links"
    )
    assert src_b.total_internal_links == seed.src_b_internal_links, (
        "Source B must have exactly 2 internal links"
    )

    # Cross-source edge must NOT be counted toward either source
    assert src_a.total_internal_links + src_b.total_internal_links == 5
    assert src_a.total_internal_links + src_b.total_internal_links < seed.total_edges


# ---------------------------------------------------------------------------
# Test 5: empty graph
# ---------------------------------------------------------------------------


def test_build_empty_graph_returns_empty_sources(integration_adapter: SqliteAdapter) -> None:
    """Fresh adapter with no seeded data must return a valid but empty GraphBreakdown."""
    svc = BuildGraphSnapshotService.from_adapter(integration_adapter)
    result = svc.build("default")

    assert result.database_name == "default"
    assert result.sources == []
    assert result.stats.total_nodes == 0
    assert result.stats.total_edges == 0
    assert result.stats.total_sources == 0

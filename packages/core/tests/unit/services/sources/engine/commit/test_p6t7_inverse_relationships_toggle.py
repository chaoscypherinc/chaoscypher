# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 6 Task 7 (2026-05-08): enable_inverse_relationships toggle on prepare_relationship_edges.

Tests confirm that:
- When ``enable_inverse_relationships=True`` (default), inverse edges are
  created for edge types that have a declared inverse in ``inverse_relationships``.
- When ``enable_inverse_relationships=False``, the inverse lookup is skipped and
  only forward edges are emitted.
- Symmetric pairs (A → A) are never doubled regardless of the toggle.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.commit.relation import (
    RelationshipCommitHandler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph_repo(
    *,
    upsert_side_effect: Any = None,
) -> MagicMock:
    """Return a minimal mock GraphRepositoryProtocol."""
    repo = MagicMock()
    # batch_create_edge_templates returns (type_to_id, created_ids, used_ids, inserted)
    repo.create_edge_template = MagicMock(return_value="tpl_1")
    repo.batch_upsert_edge_templates = MagicMock(
        side_effect=upsert_side_effect
        if upsert_side_effect is not None
        else lambda templates, **kw: [("tpl_x", "edge_type", False)]
    )
    repo.get_edge_template_by_name = MagicMock(return_value=None)
    return repo


def _make_handler(repo: Any | None = None) -> RelationshipCommitHandler:
    if repo is None:
        repo = _make_graph_repo()
    return RelationshipCommitHandler(graph_repository=repo)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnableInverseRelationshipsDefault:
    """When enable_inverse_relationships=True, inverse pairs are included."""

    @pytest.mark.asyncio
    async def test_inverse_edge_type_added_to_unique_set(self) -> None:
        """Inverse type is included in unique_edge_types when toggle is on."""
        collected_templates: list[Any] = []
        repo = MagicMock()

        async def _fake_batch(
            handler: RelationshipCommitHandler,
            edge_types: list[str],
            source_id: str | None,
            edge_descriptions: dict,
            edge_visuals: dict | None,
        ) -> tuple:
            # Record the edge types the handler sent for template creation
            collected_templates.extend(edge_types)
            return (
                {et: f"tpl_{et}" for et in edge_types},
                [f"tpl_{et}" for et in edge_types],
                [f"tpl_{et}" for et in edge_types],
                len(edge_types),
            )

        handler = _make_handler(repo)
        # Monkeypatch batch_create_edge_templates at instance level
        handler.batch_create_edge_templates = lambda *args, **kwargs: _fake_batch(  # type: ignore[method-assign]
            handler, *args, **kwargs
        )

        await handler.prepare_relationship_edges(
            relationships=[{"source": 0, "target": 1, "type": "employs"}],
            entity_name_to_node_id={},
            entity_index_to_node_id={0: "n0", 1: "n1"},
            source_id="src1",
            inverse_relationships={"employs": "is_employed_by"},
            enable_inverse_relationships=True,
        )

        assert "employs" in collected_templates
        assert "is_employed_by" in collected_templates

    @pytest.mark.asyncio
    async def test_symmetric_pair_not_doubled(self) -> None:
        """A → A symmetric pairs produce exactly one template entry."""
        collected_templates: list[Any] = []

        handler = _make_handler()

        async def _fake_batch(
            edge_types: list[str],
            source_id: str | None = None,
            edge_descriptions: dict | None = None,
            edge_visuals: Any = None,
        ) -> tuple:
            collected_templates.extend(edge_types)
            return (
                {et: f"tpl_{et}" for et in edge_types},
                [f"tpl_{et}" for et in edge_types],
                [f"tpl_{et}" for et in edge_types],
                len(edge_types),
            )

        handler.batch_create_edge_templates = _fake_batch  # type: ignore[method-assign]

        await handler.prepare_relationship_edges(
            relationships=[{"source": 0, "target": 1, "type": "related_to"}],
            entity_name_to_node_id={},
            entity_index_to_node_id={0: "n0", 1: "n1"},
            source_id="src1",
            # Symmetric: related_to ↔ related_to
            inverse_relationships={"related_to": "related_to"},
            enable_inverse_relationships=True,
        )

        # Only one "related_to" entry — symmetric loop filtered by `inverse_type != edge_type`
        assert collected_templates.count("related_to") == 1


class TestEnableInverseRelationshipsDisabled:
    """When enable_inverse_relationships=False, inverse pairs are skipped."""

    @pytest.mark.asyncio
    async def test_inverse_type_NOT_added_when_toggle_off(self) -> None:
        """Inverse type is absent from unique_edge_types when toggle is False."""
        collected_templates: list[Any] = []

        handler = _make_handler()

        async def _fake_batch(
            edge_types: list[str],
            source_id: str | None = None,
            edge_descriptions: dict | None = None,
            edge_visuals: Any = None,
        ) -> tuple:
            collected_templates.extend(edge_types)
            return (
                {et: f"tpl_{et}" for et in edge_types},
                [f"tpl_{et}" for et in edge_types],
                [f"tpl_{et}" for et in edge_types],
                len(edge_types),
            )

        handler.batch_create_edge_templates = _fake_batch  # type: ignore[method-assign]

        await handler.prepare_relationship_edges(
            relationships=[{"source": 0, "target": 1, "type": "employs"}],
            entity_name_to_node_id={},
            entity_index_to_node_id={0: "n0", 1: "n1"},
            source_id="src1",
            inverse_relationships={"employs": "is_employed_by"},
            enable_inverse_relationships=False,
        )

        assert "employs" in collected_templates
        assert "is_employed_by" not in collected_templates, (
            "Inverse type must not appear when enable_inverse_relationships=False"
        )

    @pytest.mark.asyncio
    async def test_empty_relationships_returns_empty_with_toggle_off(self) -> None:
        """Empty input returns early regardless of the toggle value."""
        handler = _make_handler()
        result = await handler.prepare_relationship_edges(
            relationships=[],
            entity_name_to_node_id={},
            entity_index_to_node_id={},
            source_id="src1",
            inverse_relationships={"x": "y"},
            enable_inverse_relationships=False,
        )
        edges, created_tpls, used_tpls, inserted = result
        assert edges == []
        assert created_tpls == []
        assert used_tpls == []
        assert inserted == 0

    @pytest.mark.asyncio
    async def test_default_is_true(self) -> None:
        """Default value of enable_inverse_relationships is True (no arg required)."""
        import inspect

        sig = inspect.signature(RelationshipCommitHandler.prepare_relationship_edges)
        default = sig.parameters["enable_inverse_relationships"].default
        assert default is True, (
            "enable_inverse_relationships must default to True to preserve "
            "backwards-compatible behaviour for callers that predate Phase 6."
        )

# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CRUD-wrapper + helper coverage for the bootstrap ``Engine``.

Drives the dict→model convenience wrappers against a real, file-backed
Engine (tmp SQLite, ``initialize_db=True``) — the highest-ROI surface in
``bootstrap.py`` because the wrappers are pure translation over the
service layer. Also covers ``_to_model`` key-filtering, ``_to_paginated``,
``_get_or_create_template`` cache hit/miss/create, ``chunk_document`` (with
a stubbed chunking service), ``commit`` raising ``NotFoundError`` when no
chunks exist, ``_ensure_source_row`` create-vs-update idempotency, and the
remaining lazy ``@property`` getters.

The real-Engine fixture mirrors ``tests/unit/test_bootstrap.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core import Engine
from chaoscypher_core.models import (
    EdgeCreate,
    EdgeUpdate,
    NodeCreate,
    NodeUpdate,
    PaginatedResult,
    TemplateCreate,
    TemplateUpdate,
)


@pytest.fixture
def engine(tmp_path):
    """Create an Engine instance with a temporary database directory."""
    db_dir = tmp_path / "databases" / "crud"
    db_dir.mkdir(parents=True)
    eng = Engine(str(db_dir), initialize_db=True)
    yield eng
    eng.close()


# ------------------------------------------------------------------ #
#  Template CRUD
# ------------------------------------------------------------------ #


class TestTemplateCrud:
    def test_create_get_update_delete_round_trip(self, engine):
        from chaoscypher_core.models import Template

        created = engine.create_template(
            TemplateCreate(name="Person", template_type="node", description="people")
        )
        assert isinstance(created, Template)
        assert created.name == "Person"

        fetched = engine.get_template(created.id)
        assert fetched.id == created.id

        updated = engine.update_template(created.id, TemplateUpdate(description="updated desc"))
        assert updated.description == "updated desc"

        engine.delete_template(created.id)
        from chaoscypher_core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            engine.get_template(created.id)

    def test_list_templates_paginated(self, engine):
        for i in range(3):
            engine.create_template(TemplateCreate(name=f"T{i}", template_type="node"))
        page = engine.list_templates(template_type="node", page=1, page_size=2)
        assert isinstance(page, PaginatedResult)
        assert page.page == 1
        assert page.page_size == 2
        assert len(page.data) == 2
        assert page.total >= 3


# ------------------------------------------------------------------ #
#  Node CRUD
# ------------------------------------------------------------------ #


class TestNodeCrud:
    def test_create_get_update_delete_round_trip(self, engine):
        from chaoscypher_core.models import Node

        tmpl = engine.create_template(TemplateCreate(name="Thing", template_type="node"))
        node = engine.create_node(
            NodeCreate(template_id=tmpl.id, label="Widget", properties={"k": "v"})
        )
        assert isinstance(node, Node)
        assert node.label == "Widget"

        fetched = engine.get_node(node.id)
        assert fetched.id == node.id

        updated = engine.update_node(node.id, NodeUpdate(label="Gadget"))
        assert updated.label == "Gadget"

        engine.delete_node(node.id)
        from chaoscypher_core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            engine.get_node(node.id)

    def test_list_nodes_paginated(self, engine):
        tmpl = engine.create_template(TemplateCreate(name="N", template_type="node"))
        for i in range(3):
            engine.create_node(NodeCreate(template_id=tmpl.id, label=f"n{i}"))
        page = engine.list_nodes(page=1, page_size=10)
        assert isinstance(page, PaginatedResult)
        assert page.total >= 3


# ------------------------------------------------------------------ #
#  Edge CRUD
# ------------------------------------------------------------------ #


class TestEdgeCrud:
    def test_create_get_update_delete_and_list(self, engine):
        from chaoscypher_core.models import Edge

        node_tmpl = engine.create_template(TemplateCreate(name="Node", template_type="node"))
        edge_tmpl = engine.create_template(TemplateCreate(name="Rel", template_type="edge"))
        a = engine.create_node(NodeCreate(template_id=node_tmpl.id, label="A"))
        b = engine.create_node(NodeCreate(template_id=node_tmpl.id, label="B"))

        edge = engine.create_edge(
            EdgeCreate(
                template_id=edge_tmpl.id,
                source_node_id=a.id,
                target_node_id=b.id,
                label="knows",
            )
        )
        assert isinstance(edge, Edge)

        fetched = engine.get_edge(edge.id)
        assert fetched.id == edge.id

        updated = engine.update_edge(edge.id, EdgeUpdate(label="met"))
        assert updated.label == "met"

        page = engine.list_edges(source_node_id=a.id, page=1, page_size=10)
        assert isinstance(page, PaginatedResult)
        assert page.total >= 1

        engine.delete_edge(edge.id)
        from chaoscypher_core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            engine.get_edge(edge.id)


# ------------------------------------------------------------------ #
#  add_node / add_edge (get-or-create template)
# ------------------------------------------------------------------ #


class TestQuickGraphBuilding:
    def test_add_node_and_edge(self, engine):
        alice = engine.add_node("Person", "Alice", properties={"role": "eng"})
        bob = engine.add_node("Person", "Bob")
        edge = engine.add_edge("knows", alice, bob)
        assert edge.source_node_id == alice.id
        assert edge.target_node_id == bob.id
        assert edge.label == "knows"

    def test_add_edge_accepts_string_ids(self, engine):
        a = engine.add_node("Person", "A")
        b = engine.add_node("Person", "B")
        edge = engine.add_edge("likes", a.id, b.id, label="fancies")
        assert edge.label == "fancies"


# ------------------------------------------------------------------ #
#  _get_or_create_template cache hit/miss/create
# ------------------------------------------------------------------ #


class TestGetOrCreateTemplate:
    def test_create_then_cache_hit(self, engine):
        tid1 = engine._get_or_create_template("Animal", "node")
        # Second call must return the cached ID without re-creating.
        tid2 = engine._get_or_create_template("Animal", "node")
        assert tid1 == tid2
        assert ("Animal", "node") in engine._template_cache

    def test_miss_then_db_lookup_populates_cache(self, engine):
        # Create a template directly through the service, clear the cache,
        # then resolve it — exercises the DB-lookup (miss) branch.
        engine.template_service.create_template(TemplateCreate(name="Plant", template_type="node"))
        engine._template_cache.clear()
        tid = engine._get_or_create_template("Plant", "node")
        assert tid
        assert engine._template_cache[("Plant", "node")] == tid


# ------------------------------------------------------------------ #
#  _to_model / _to_paginated
# ------------------------------------------------------------------ #


class TestToModelHelpers:
    def test_to_model_filters_unknown_keys(self):
        from chaoscypher_core.models import Template

        data = {
            "id": "t1",
            "name": "X",
            "template_type": "node",
            "bogus_field": "ignored",
            "another_extra": 123,
        }
        model = Engine._to_model(Template, data)
        assert model.id == "t1"
        assert model.name == "X"
        assert not hasattr(model, "bogus_field")

    def test_to_paginated_builds_models(self, engine):
        from chaoscypher_core.models import Node

        result = {
            "data": [
                {"id": "n1", "label": "L1", "template_id": "t", "extra": "drop"},
                {"id": "n2", "label": "L2", "template_id": "t"},
            ],
            "pagination": {
                "total": 2,
                "page": 1,
                "page_size": 50,
                "total_pages": 1,
                "has_next": False,
                "has_prev": False,
            },
        }
        paginated = engine._to_paginated(Node, result)
        assert isinstance(paginated, PaginatedResult)
        assert len(paginated.data) == 2
        assert all(isinstance(item, Node) for item in paginated.data)
        assert paginated.data[0].id == "n1"


# ------------------------------------------------------------------ #
#  chunk_document / commit
# ------------------------------------------------------------------ #


class TestChunkAndCommit:
    @pytest.mark.asyncio
    async def test_chunk_document_returns_result_with_generated_source_id(self, engine):
        from chaoscypher_core.models import ChunkingResult

        fake = MagicMock()
        fake.total_small_chunks = 4
        fake.total_groups = 2
        engine.chunking_service.create_chunks = AsyncMock(return_value=fake)

        result = await engine.chunk_document("some text", analysis_depth="quick")
        assert isinstance(result, ChunkingResult)
        assert result.total_small_chunks == 4
        assert result.total_groups == 2
        assert result.analysis_depth == "quick"
        assert result.source_id  # auto-generated, non-empty
        engine.chunking_service.create_chunks.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_chunk_document_honors_explicit_source_id(self, engine):
        fake = MagicMock(total_small_chunks=1, total_groups=1)
        engine.chunking_service.create_chunks = AsyncMock(return_value=fake)
        result = await engine.chunk_document("t", source_id="src_explicit")
        assert result.source_id == "src_explicit"

    @pytest.mark.asyncio
    async def test_commit_raises_not_found_when_no_chunks(self, engine):
        from chaoscypher_core.exceptions import NotFoundError

        engine.chunking_service.get_small_chunks = MagicMock(return_value=[])
        with pytest.raises(NotFoundError):
            await engine.commit("missing_source")


# ------------------------------------------------------------------ #
#  _ensure_source_row
# ------------------------------------------------------------------ #


class TestEnsureSourceRow:
    def test_creates_new_source_row(self, engine):
        engine._ensure_source_row(
            source_id="src_new",
            filename="report.pdf",
            analysis_depth="full",
            confirmation_required=True,
            forced_domain=None,
        )
        row = engine.storage_adapter.get_source("src_new", engine.settings.current_database)
        assert row is not None
        assert row["filename"] == "report.pdf"
        assert row["file_type"] == "pdf"

    def test_idempotent_update_preserves_filename(self, engine):
        engine._ensure_source_row(
            source_id="src_dup",
            filename="original.txt",
            analysis_depth="full",
            confirmation_required=False,
            forced_domain=None,
        )
        # Second call with a different filename only stamps gate fields; the
        # existing row's filename must be untouched.
        engine._ensure_source_row(
            source_id="src_dup",
            filename="ignored.txt",
            analysis_depth="quick",
            confirmation_required=True,
            forced_domain="medical",
        )
        row = engine.storage_adapter.get_source("src_dup", engine.settings.current_database)
        assert row["filename"] == "original.txt"
        assert row.get("forced_domain") == "medical"

    def test_forced_domain_persisted_on_create(self, engine):
        engine._ensure_source_row(
            source_id="src_forced",
            filename="doc.md",
            analysis_depth="full",
            confirmation_required=False,
            forced_domain="legal",
        )
        row = engine.storage_adapter.get_source("src_forced", engine.settings.current_database)
        assert row.get("forced_domain") == "legal"


# ------------------------------------------------------------------ #
#  Lazy property getters
# ------------------------------------------------------------------ #


class TestEngineLazyPropertiesExtra:
    def test_retry_policy_lazy_and_cached(self, engine):
        assert engine._retry_policy is None
        policy = engine.retry_policy
        assert policy is not None
        assert engine.retry_policy is policy

    def test_embedding_provider_aliases_service(self, engine):
        assert engine.embedding_provider is engine.embedding_service

    def test_get_stats_returns_counts(self, engine):
        engine.add_node("Person", "Solo")
        stats = engine.get_stats()
        assert stats.database_name == engine.database_name
        assert stats.nodes >= 1

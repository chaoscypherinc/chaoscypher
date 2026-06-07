# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for commit idempotency via stable-key UPSERTs.

The commit phase has historically used fresh UUIDs for every INSERT,
which made the whole phase non-idempotent: re-running commit for the
same source would silently double the graph. Resumability requires
crash-resume on commit, so every commit-time INSERT gets an
SHA256-derived stable key plus SELECT-then-INSERT dedup.

These tests pin the idempotency guarantee at the repo layer for the
upsert_* methods. Task 11 covers nodes; Task 12 extends the same
pattern to edges, templates, and citations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.adapters.sqlite.models import GraphTemplate
from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.models import (
    EdgeCreate,
    NodeCreate,
    PropertyDefinition,
    PropertyType,
    TemplateCreate,
)


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


def _seed_source(adapter: SqliteAdapter, source_id: str) -> None:
    """Create the minimum viable source row so graph_nodes FK is satisfied."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.txt",
            "filepath": f"/tmp/{source_id}.txt",
            "file_type": "text",
            "file_size": 123,
            "content_hash": f"hash-{source_id}",
            "status": "committing",
        }
    )


def _seed_template(adapter: SqliteAdapter, template_id: str) -> None:
    """Create a template so graph_{nodes,edges}.template_id FK is satisfied."""
    adapter.session.add(
        GraphTemplate(
            id=template_id,
            database_name=adapter.database_name,
            name=f"name-{template_id}",
            template_type="node",
        )
    )
    adapter.session.flush()


@pytest.fixture
def graph_repo(in_memory_adapter: SqliteAdapter) -> GraphRepository:
    """Construct a GraphRepository sharing the adapter's session.

    Reusing the session means both the adapter and the graph repo
    write through the same transaction context and see each other's
    commits. Seeds two source rows (src-1, src-2) so FK constraints
    on graph_nodes.source_id are satisfied.
    """
    _seed_source(in_memory_adapter, "src-1")
    _seed_source(in_memory_adapter, "src-2")
    return GraphRepository(
        session=in_memory_adapter.session,
        database_name=in_memory_adapter.database_name,
    )


@pytest.fixture
def graph_repo_with_node_templates(
    graph_repo: GraphRepository,
    in_memory_adapter: SqliteAdapter,
) -> GraphRepository:
    """graph_repo plus the 'tpl-person' / 'tpl-knows' templates pre-seeded.

    Node- and edge-upsert tests reference these template IDs; with the
    FK on graph_{nodes,edges}.template_id declared, the referenced rows
    must exist before the upsert tries to insert. Template-only tests
    should stick with the base ``graph_repo`` to keep their
    ``count_templates`` assertions precise.
    """
    _seed_template(in_memory_adapter, "tpl-person")
    _seed_template(in_memory_adapter, "tpl-knows")
    return graph_repo


@pytest.mark.asyncio
async def test_upsert_nodes_batch_is_idempotent(
    graph_repo_with_node_templates: GraphRepository,
) -> None:
    """Calling upsert_nodes_batch twice with the same input produces the same node IDs.

    It does not duplicate rows.
    """
    graph_repo = graph_repo_with_node_templates
    nodes_to_create = [
        NodeCreate(
            template_id="tpl-person",
            label="Alice",
            properties={"role": "scientist"},
            source_id="src-1",
        ),
        NodeCreate(
            template_id="tpl-person",
            label="Bob",
            properties={"role": "engineer"},
            source_id="src-1",
        ),
    ]

    first, first_inserted = await graph_repo.upsert_nodes_batch(nodes_to_create)
    assert len(first) == 2
    assert first_inserted == 2
    first_ids = {n.id for n in first}

    # Second call with identical input: same IDs, no new rows
    second, second_inserted = await graph_repo.upsert_nodes_batch(nodes_to_create)
    assert len(second) == 2
    assert second_inserted == 0
    second_ids = {n.id for n in second}
    assert first_ids == second_ids

    # And the database really has only two rows, not four
    total = graph_repo.count_nodes()
    assert total == 2


@pytest.mark.asyncio
async def test_upsert_nodes_batch_distinguishes_by_source_id(
    graph_repo_with_node_templates: GraphRepository,
) -> None:
    """Two sources that both mention "Alice" get distinct node rows.

    Source-scoped identity is the whole point of the stable key:
    provenance stays correct even when different documents use the
    same labels.
    """
    graph_repo = graph_repo_with_node_templates
    src_1_nodes = [
        NodeCreate(
            template_id="tpl-person",
            label="Alice",
            properties={},
            source_id="src-1",
        ),
    ]
    src_2_nodes = [
        NodeCreate(
            template_id="tpl-person",
            label="Alice",
            properties={},
            source_id="src-2",
        ),
    ]

    first, _ = await graph_repo.upsert_nodes_batch(src_1_nodes)
    second, _ = await graph_repo.upsert_nodes_batch(src_2_nodes)

    assert first[0].id != second[0].id
    assert graph_repo.count_nodes() == 2


@pytest.mark.asyncio
async def test_upsert_nodes_batch_is_case_insensitive_on_label(
    graph_repo_with_node_templates: GraphRepository,
) -> None:
    """Stable key normalizes label so 'Alice' and '  alice ' map to the same row.

    This matches the extractor's existing canonicalization so tiny
    label whitespace drift across runs doesn't break dedup.
    """
    graph_repo = graph_repo_with_node_templates
    variant_a = [
        NodeCreate(
            template_id="tpl-person",
            label="Alice",
            properties={},
            source_id="src-1",
        ),
    ]
    variant_b = [
        NodeCreate(
            template_id="tpl-person",
            label="  alice ",
            properties={},
            source_id="src-1",
        ),
    ]

    first, _ = await graph_repo.upsert_nodes_batch(variant_a)
    second, _ = await graph_repo.upsert_nodes_batch(variant_b)

    assert first[0].id == second[0].id
    assert graph_repo.count_nodes() == 1


@pytest.mark.asyncio
async def test_upsert_edges_batch_is_idempotent(
    graph_repo_with_node_templates: GraphRepository,
) -> None:
    """Calling upsert_edges_batch twice with the same input produces the same edge IDs.

    Does not duplicate rows. Relies on upsert_nodes_batch having
    produced stable endpoint IDs; otherwise the edge hash would drift
    across runs.
    """
    graph_repo = graph_repo_with_node_templates
    # First ensure the endpoint nodes exist with stable IDs
    nodes = [
        NodeCreate(
            template_id="tpl-person",
            label="Alice",
            properties={},
            source_id="src-1",
        ),
        NodeCreate(
            template_id="tpl-person",
            label="Bob",
            properties={},
            source_id="src-1",
        ),
    ]
    created_nodes, _ = await graph_repo.upsert_nodes_batch(nodes)
    alice_id, bob_id = created_nodes[0].id, created_nodes[1].id

    edges = [
        EdgeCreate(
            template_id="tpl-knows",
            source_node_id=alice_id,
            target_node_id=bob_id,
            label="knows",
            properties={},
            source_id="src-1",
        ),
    ]

    first, first_inserted = await graph_repo.upsert_edges_batch(edges)
    assert len(first) == 1
    assert first_inserted == 1

    # Second call with identical input: same ID, no duplicate row
    second, second_inserted = await graph_repo.upsert_edges_batch(edges)
    assert len(second) == 1
    assert second_inserted == 0
    assert first[0].id == second[0].id
    assert graph_repo.count_edges() == 1


@pytest.mark.asyncio
async def test_upsert_edges_batch_deduplicates_within_batch(
    graph_repo_with_node_templates: GraphRepository,
) -> None:
    """Duplicate stable IDs within a single batch must not cause IntegrityError.

    Cross-chunk extraction can produce multiple EdgeCreate objects that
    map to the same stable edge ID.  First-write-wins within the batch.
    """
    graph_repo = graph_repo_with_node_templates
    nodes = [
        NodeCreate(
            template_id="tpl-person",
            label="Alice",
            properties={},
            source_id="src-1",
        ),
        NodeCreate(
            template_id="tpl-person",
            label="Bob",
            properties={},
            source_id="src-1",
        ),
    ]
    created_nodes, _ = await graph_repo.upsert_nodes_batch(nodes)
    alice_id, bob_id = created_nodes[0].id, created_nodes[1].id

    # Two EdgeCreate objects that produce the same stable ID
    edges = [
        EdgeCreate(
            template_id="tpl-knows",
            source_node_id=alice_id,
            target_node_id=bob_id,
            label="knows",
            properties={"chunk_index": 0},
            source_id="src-1",
        ),
        EdgeCreate(
            template_id="tpl-knows",
            source_node_id=alice_id,
            target_node_id=bob_id,
            label="knows",
            properties={"chunk_index": 3},
            source_id="src-1",
        ),
    ]

    result, inserted = await graph_repo.upsert_edges_batch(edges)
    assert len(result) == 2
    assert inserted == 1  # Only one unique edge was actually inserted
    assert result[0].id == result[1].id
    assert graph_repo.count_edges() == 1


@pytest.mark.asyncio
async def test_upsert_template_is_idempotent(
    graph_repo: GraphRepository,
) -> None:
    """Calling upsert_template twice with the same input reuses the row."""
    template_create = TemplateCreate(
        name="Person",
        description="A person entity",
        template_type="node",
        properties=[
            PropertyDefinition(
                name="name",
                display_name="Name",
                property_type=PropertyType.TEXT,
                required=True,
            ),
        ],
        icon=None,
        color=None,
        source_id="src-1",
    )

    first, first_is_new = graph_repo.upsert_template(template_create)
    second, second_is_new = graph_repo.upsert_template(template_create)

    assert first.id == second.id
    assert first_is_new is True
    assert second_is_new is False
    assert graph_repo.count_templates(database_name="default") == 1


@pytest.mark.asyncio
async def test_upsert_templates_batch_is_idempotent(
    graph_repo: GraphRepository,
) -> None:
    """Batch variant of upsert_template: same input twice = same IDs."""
    templates = [
        TemplateCreate(
            name="Person",
            template_type="node",
            properties=[],
            source_id="src-1",
        ),
        TemplateCreate(
            name="Organization",
            template_type="node",
            properties=[],
            source_id="src-1",
        ),
    ]

    first, first_inserted = await graph_repo.upsert_templates_batch(templates)
    second, second_inserted = await graph_repo.upsert_templates_batch(templates)

    assert len(first) == len(second) == 2
    assert {t.id for t in first} == {t.id for t in second}
    assert first_inserted == 2
    assert second_inserted == 0
    assert graph_repo.count_templates(database_name="default") == 2


@pytest.mark.asyncio
async def test_commit_handler_fast_path_when_complete() -> None:
    """When the source.commit_complete flag is already set, the handler returns immediately.

    It does not construct SourceCommitService or touch the graph.
    """
    from unittest.mock import MagicMock, patch

    from chaoscypher_core.operations.importing.import_service import (
        ImportOperationsService,
    )

    adapter = MagicMock()
    adapter.get_source = MagicMock(
        return_value={
            "id": "src-1",
            "status": "committed",
            "commit_complete": True,
            "database_name": "default",
            "is_paused": False,
        }
    )
    adapter.get_system_state = MagicMock(return_value={"processing_paused": False})
    adapter.update_source_last_activity = MagicMock()

    service = ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=MagicMock(),
        source_repository=adapter,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
    )

    # get_settings is a lazy import inside the handler, patch it at
    # its real module location
    fake_settings = MagicMock()
    fake_settings.current_database = "default"
    with patch(
        "chaoscypher_core.app_config.get_settings",
        return_value=fake_settings,
    ):
        result = await service._import_commit_handler(
            data={
                "file_id": "src-1",
                "commit_data": {},
                "file_info": {},
            },
        )

    assert result["skipped"] == "already_committed"
    assert result["file_id"] == "src-1"
    # Fast path did NOT touch last_activity (we only touch on real work)
    adapter.update_source_last_activity.assert_not_called()


@pytest.mark.asyncio
async def test_commit_self_heal_does_not_overwrite_persisted_counts() -> None:
    """F54: self-heal must NOT re-call complete_commit() with persisted counts.

    Reasoning: complete_commit() unconditionally overwrites the count
    columns with whatever it is given. If a partial-commit-then-crash (or
    any future bug) leaves wrong counts on a row that already has
    commit_complete=True, re-running the handler must not lock those wrong
    counts in by re-persisting them. The fast path must be a pure
    idempotent return on a row that's already in (commit_complete=True,
    status=COMMITTED).
    """
    from unittest.mock import MagicMock, patch

    from chaoscypher_core.operations.importing.import_service import (
        ImportOperationsService,
    )

    adapter = MagicMock()
    adapter.get_source = MagicMock(
        return_value={
            "id": "src-1",
            "status": "committed",  # already aligned with commit_complete
            "commit_complete": True,
            "commit_nodes_created": 999,
            "commit_edges_created": 42,
            "commit_templates_created": 7,
            "source_document_node_id": "doc-1",
            "database_name": "default",
            "is_paused": False,
        }
    )
    adapter.get_system_state = MagicMock(return_value={"processing_paused": False})

    graph_repo = MagicMock()
    service = ImportOperationsService(
        graph_repository=graph_repo,
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=MagicMock(),
        source_repository=adapter,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
    )

    fake_settings = MagicMock()
    fake_settings.current_database = "default"
    with patch(
        "chaoscypher_core.app_config.get_settings",
        return_value=fake_settings,
    ):
        result = await service._import_commit_handler(
            data={
                "file_id": "src-1",
                "commit_data": {},
                "file_info": {},
            },
        )

    # Idempotent return
    assert result["skipped"] == "already_committed"
    assert result["file_id"] == "src-1"

    # CRITICAL: complete_commit must NOT be re-called when status is
    # already COMMITTED. If it were, it would re-persist whatever counts
    # came back from get_source — even drifted ones — and overwrite
    # commit_completed_at with a fresh timestamp.
    adapter.complete_commit.assert_not_called()

    # And the graph must NOT be touched
    graph_repo.upsert_nodes_batch.assert_not_called()
    graph_repo.upsert_edges_batch.assert_not_called()
    graph_repo.upsert_templates_batch.assert_not_called()


@pytest.mark.asyncio
async def test_commit_self_heal_emits_count_drift_warning(
    caplog: pytest.LogCaptureFixture,
    structlog_for_caplog: None,  # pytest fixture, side-effect only
) -> None:
    """F54: legacy rows with commit_complete=True but zero counts emit a drift log.

    A row with ``commit_complete=True`` but every count column at 0 (or
    None) is suspicious — either a legacy state from before counts were
    tracked, or a partial-commit-then-flag-set bug. Self-heal does NOT
    auto-fix the counts (we don't know the true graph state without
    re-counting), but it MUST emit ``commit_self_heal_count_drift`` so
    operators can investigate. The handler still returns success
    idempotently.
    """
    import logging as _logging
    from unittest.mock import MagicMock, patch

    from chaoscypher_core.operations.importing.import_service import (
        ImportOperationsService,
    )

    adapter = MagicMock()
    adapter.get_source = MagicMock(
        return_value={
            "id": "src-1",
            "status": "committed",
            "commit_complete": True,
            # Drift: flag is True but counts are zero/None
            "commit_nodes_created": 0,
            "commit_edges_created": 0,
            "commit_templates_created": None,
            "source_document_node_id": None,
            "database_name": "default",
            "is_paused": False,
        }
    )
    adapter.get_system_state = MagicMock(return_value={"processing_paused": False})

    service = ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=MagicMock(),
        source_repository=adapter,
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
    )

    fake_settings = MagicMock()
    fake_settings.current_database = "default"
    with (
        patch(
            "chaoscypher_core.app_config.get_settings",
            return_value=fake_settings,
        ),
        caplog.at_level(_logging.WARNING),
    ):
        result = await service._import_commit_handler(
            data={
                "file_id": "src-1",
                "commit_data": {},
                "file_info": {},
            },
        )

    # Drift warning emitted
    combined = " ".join(r.getMessage() for r in caplog.records)
    assert "commit_self_heal_count_drift" in combined, (
        f"Expected commit_self_heal_count_drift in logs, got: {combined}"
    )

    # Counts NOT auto-fixed (we don't know the truth without recount)
    adapter.complete_commit.assert_not_called()

    # Idempotent success return
    assert result["skipped"] == "already_committed"


def test_create_citations_batch_is_idempotent(
    in_memory_adapter: SqliteAdapter,
) -> None:
    """Passing the same stable-keyed citation twice is a no-op on replay."""
    _seed_source(in_memory_adapter, "src-1")
    # Seed a document chunk so the chunk_id FK is satisfied
    in_memory_adapter.session.execute(
        __import__("sqlalchemy").text(
            "INSERT INTO document_chunks (id, database_name, source_id, "
            "chunk_index, content, status, created_at, citation_offset_method) VALUES "
            "('chunk-1', 'default', 'src-1', 0, 'text', 'indexed', "
            "'2026-04-11T00:00:00+00:00', 'exact')"
        )
    )
    in_memory_adapter.session.commit()

    citation = {
        "id": "cite_stable_test_001",
        "database_name": "default",
        "entity_uri": "node_abc",
        "entity_label": "Alice",
        "entity_type": "Person",
        "source_id": "src-1",
        "chunk_id": "chunk-1",
        "confidence": 0.9,
        "extraction_method": "ai_extraction",
    }

    # First call creates the row
    first = in_memory_adapter.create_citations_batch([dict(citation)])
    assert len(first) == 1
    assert first[0]["id"] == "cite_stable_test_001"

    # Second call with the SAME id observes the existing row and
    # returns it without raising a PK conflict
    second = in_memory_adapter.create_citations_batch([dict(citation)])
    assert len(second) == 1
    assert second[0]["id"] == "cite_stable_test_001"

    # Database still contains exactly one row
    count_stmt = __import__("sqlalchemy").text(
        "SELECT COUNT(*) FROM source_citations WHERE id = 'cite_stable_test_001'"
    )
    assert in_memory_adapter.session.execute(count_stmt).scalar() == 1

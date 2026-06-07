# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Crash-injection and ordering tests for SourceCommitService.commit()."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.exc import OperationalError


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter

from chaoscypher_core.adapters.sqlite.repos import GraphRepository
from chaoscypher_core.models import SourceStatus
from chaoscypher_core.services.sources.engine.commit.service import (
    SourceCommitService,
)


@pytest.fixture
def seeded_source(adapter_with_default_templates: SqliteAdapter) -> str:
    """Seed an extracted source row ready for commit.

    Uses the ``adapter_with_default_templates`` fixture so the commit-time
    fallback to ``system_template_item`` satisfies the FK on
    ``graph_{nodes,edges}.template_id``.
    """
    in_memory_adapter = adapter_with_default_templates
    source_id = "src_1"
    in_memory_adapter.create_source(
        {
            "id": source_id,
            "database_name": in_memory_adapter.database_name,
            "filename": "doc.md",
            "filepath": "/tmp/doc.md",
            "file_type": "markdown",
            "file_size": 123,
            "content_hash": "hash-1",
            "status": SourceStatus.EXTRACTED.value,
        }
    )
    in_memory_adapter.complete_extraction(
        source_id=source_id,
        entities=[
            {"name": "Alice", "type": "Person", "properties": {}},
            {"name": "Acme", "type": "Organization", "properties": {}},
        ],
        relationships=[
            {"source": 0, "target": 1, "type": "works_at"},
        ],
        forced_domain=None,
        detected_domain="technical",
    )
    return source_id


def _build_commit_service(
    in_memory_adapter: SqliteAdapter,
) -> SourceCommitService:
    """Construct SourceCommitService with real repositories sharing the adapter session.

    Mirrors the production wiring at
    packages/cortex/.../import_service.py where the cortex API constructs
    the service. Uses the adapter directly as source_repository,
    sources_repository, and indexing_repository because SqliteAdapter
    implements all three storage protocols via its mixins.
    """
    from chaoscypher_core.adapters.sqlite.engine import get_engine
    from chaoscypher_core.adapters.sqlite.repos import SearchRepository
    from chaoscypher_core.settings import EngineSettings

    graph_repository = GraphRepository(
        session=in_memory_adapter.session,
        database_name=in_memory_adapter.database_name,
    )
    settings = EngineSettings()
    # Use a small fixed vector_dim and placeholder model name — tests don't
    # hit the LLM in the crash path so no real embeddings are generated.
    # The same pattern is used in test_search_repository.py.
    search_repository = SearchRepository(
        engine=get_engine(in_memory_adapter.db_path),
        vector_dim=4,
        embedding_model="test-model",
    )
    return SourceCommitService(
        graph_repository=graph_repository,
        source_repository=in_memory_adapter,
        sources_repository=in_memory_adapter,
        indexing_repository=in_memory_adapter,
        search_repository=search_repository,
        settings=settings,
    )


@pytest.mark.asyncio
async def test_commit_rolls_back_on_mid_write_crash(
    in_memory_adapter: SqliteAdapter, seeded_source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected exception during node creation rolls back the entire commit.

    Verifies atomicity: a crash partway through commit must leave the
    source in its pre-commit state (status='extracted', no partial graph
    data) so recovery can safely re-dispatch.
    """
    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.models import GraphNode, SourceRow

    service = _build_commit_service(in_memory_adapter)

    async def raise_boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected crash during node creation")

    monkeypatch.setattr(service.entity_handler, "batch_create_nodes", raise_boom)

    commit_data = {
        "entities": [{"name": "Alice", "type": "Person", "properties": {}}],
        "relationships": [],
        "suggested_templates": [],
    }

    with pytest.raises(RuntimeError, match="injected crash"):
        await service.commit(
            file_id=seeded_source,
            commit_data=commit_data,
            file_info={
                "id": seeded_source,
                "database_name": "default",
                "filtering_mode": "unfiltered",
            },
        )

    in_memory_adapter.session.expire_all()
    row = in_memory_adapter.session.get(SourceRow, seeded_source)
    assert row.status == SourceStatus.EXTRACTED
    assert row.commit_complete is False

    nodes = list(in_memory_adapter.session.exec(select(GraphNode)))
    assert nodes == []


@pytest.mark.asyncio
async def test_commit_writes_all_entities_atomically(
    in_memory_adapter: SqliteAdapter, seeded_source: str
) -> None:
    """Successful commit writes nodes, edges, and marks source committed."""
    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphNode, SourceRow

    service = _build_commit_service(in_memory_adapter)

    commit_data = {
        "entities": [
            {"name": "Alice", "type": "Person", "properties": {}},
            {"name": "Acme", "type": "Organization", "properties": {}},
        ],
        "relationships": [
            {"source": 0, "target": 1, "type": "works_at"},
        ],
        "suggested_templates": [],
    }

    result = await service.commit(
        file_id=seeded_source,
        commit_data=commit_data,
        file_info={
            "id": seeded_source,
            "database_name": "default",
            # Atomicity tests exercise crash-recovery mechanics, not quality filters.
            # Disable orphan filtering so entities are written regardless of whether
            # the test commit_data includes name-matched relationships.
            "filtering_mode": "unfiltered",
        },
    )

    nodes = list(in_memory_adapter.session.exec(select(GraphNode)))
    edges = list(in_memory_adapter.session.exec(select(GraphEdge)))
    assert len(nodes) == 2
    assert len(edges) == 1

    row = in_memory_adapter.session.get(SourceRow, seeded_source)
    assert row.status == SourceStatus.COMMITTED
    assert row.commit_complete is True

    assert len(result["created_nodes"]) == 2
    assert len(result["created_edges"]) == 1


@pytest.mark.asyncio
async def test_commit_retry_after_failure_has_no_duplicates(
    in_memory_adapter: SqliteAdapter, seeded_source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second commit after a failed first attempt leaves exactly one copy."""
    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.models import GraphNode

    service = _build_commit_service(in_memory_adapter)

    commit_data = {
        "entities": [{"name": "Alice", "type": "Person", "properties": {}}],
        "relationships": [],
        "suggested_templates": [],
    }
    # Atomicity/dedup mechanics test — disable orphan filter so Alice is written.
    file_info = {"id": seeded_source, "database_name": "default", "filtering_mode": "unfiltered"}

    # First attempt fails mid-write
    original_batch_create = service.entity_handler.batch_create_nodes

    async def raise_boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(service.entity_handler, "batch_create_nodes", raise_boom)

    with pytest.raises(RuntimeError):
        await service.commit(seeded_source, commit_data, file_info)

    # Restore and retry (simulates recovery re-dispatch)
    monkeypatch.setattr(service.entity_handler, "batch_create_nodes", original_batch_create)
    in_memory_adapter.session.expire_all()

    await service.commit(seeded_source, commit_data, file_info)

    alice_nodes = list(
        in_memory_adapter.session.exec(select(GraphNode).where(GraphNode.label == "Alice"))
    )
    assert len(alice_nodes) == 1


@pytest.mark.asyncio
async def test_llm_calls_happen_outside_transaction(
    in_memory_adapter: SqliteAdapter, seeded_source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Template embedding must not execute while a transaction is open.

    Uses the new SafeSession._transaction_depth as the source of truth:
    inside adapter.transaction(), depth > 0; outside, depth == 0. LLM
    calls (template embedding) must observe depth == 0 every time.
    """
    service = _build_commit_service(in_memory_adapter)

    depth_during_embed: list[int] = []
    original_embed = service._embed_created_templates

    async def spy_embed(template_ids: list[str], *, session: Any | None = None) -> None:
        depth_during_embed.append(in_memory_adapter.session._transaction_depth)
        return await original_embed(template_ids, session=session)

    monkeypatch.setattr(service, "_embed_created_templates", spy_embed)

    commit_data = {
        "entities": [{"name": "Alice", "type": "Person", "properties": {}}],
        "relationships": [],
        "create_templates": True,
        "suggested_templates": [
            {"name": "Person", "description": "A person entity"},
        ],
    }

    await service.commit(
        file_id=seeded_source,
        commit_data=commit_data,
        file_info={"id": seeded_source, "database_name": "default"},
    )

    assert depth_during_embed, (
        "Template embedding was not called — verify create_templates=True is set "
        "and suggested_templates are non-empty"
    )
    assert all(depth == 0 for depth in depth_during_embed), (
        f"Template embedding observed transaction depth > 0: {depth_during_embed}"
    )


@pytest.mark.asyncio
async def test_commit_recovers_from_search_indexing_failure(
    in_memory_adapter: SqliteAdapter,
    seeded_source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-transaction search indexing failure must NOT roll back the commit.

    Search indexing runs after the main commit transaction exits. If it
    raises, the commit service rolls back the session to clear state and
    logs non-fatally — the graph data has already committed and should
    stay visible. Subsequent operations on the session must succeed.
    """
    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.models import GraphNode, SourceRow

    service = _build_commit_service(in_memory_adapter)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected-search-failure")

    # Inject failure at the first search-indexing touchpoint
    monkeypatch.setattr(service.search_repository, "index_nodes_batch", boom)

    commit_data = {
        "entities": [{"name": "Alice", "type": "Person", "properties": {}}],
        "relationships": [],
        "suggested_templates": [],
    }
    # commit() must NOT raise — the failure is logged non-fatally.
    # Use unfiltered mode so Alice is written (atomicity test, not a quality-filter test).
    result = await service.commit(
        file_id=seeded_source,
        commit_data=commit_data,
        file_info={"id": seeded_source, "database_name": "default", "filtering_mode": "unfiltered"},
    )

    # Graph data was committed by the transaction before the search failure.
    in_memory_adapter.session.expire_all()
    row = in_memory_adapter.session.get(SourceRow, seeded_source)
    assert row.status == SourceStatus.COMMITTED
    assert row.commit_complete is True

    nodes = list(in_memory_adapter.session.exec(select(GraphNode)))
    assert len(nodes) == 1
    assert len(result["created_nodes"]) == 1

    # Session must be usable afterwards — the rollback-on-failure in
    # _commit_impl clears the dirty state so subsequent code doesn't
    # hit PendingRollbackError.
    in_memory_adapter.session.exec(select(SourceRow)).all()


@pytest.mark.asyncio
async def test_first_attempt_commit_skips_cleanup(
    in_memory_adapter: SqliteAdapter,
    seeded_source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh sources (commit_nodes_created == 0) must not call _cleanup_previous_commit.

    The three DELETEs in _cleanup_previous_commit are a no-op on first attempts but
    still fight the SQLite writer lock under concurrent load. Skipping them on fresh
    commits removes a failure mode that has been observed in production.
    """
    service = _build_commit_service(in_memory_adapter)

    cleanup_calls: list[tuple[str, str]] = []
    original_cleanup = service._cleanup_previous_commit

    def spy_cleanup(file_id: str, source_id: str) -> dict[str, Any]:
        cleanup_calls.append((file_id, source_id))
        return original_cleanup(file_id, source_id)

    monkeypatch.setattr(service, "_cleanup_previous_commit", spy_cleanup)

    commit_data = {
        "entities": [{"name": "Alice", "type": "Person", "properties": {}}],
        "relationships": [],
        "suggested_templates": [],
    }
    await service.commit(
        file_id=seeded_source,
        commit_data=commit_data,
        file_info={"id": seeded_source, "database_name": "default"},
    )

    assert cleanup_calls == []


@pytest.mark.asyncio
async def test_retry_commit_runs_cleanup(
    in_memory_adapter: SqliteAdapter,
    seeded_source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-commit after a successful prior commit must call _cleanup_previous_commit.

    Simulates the manual-retry flow: a source was committed once
    (commit_nodes_created > 0, commit_complete flipped back to False by the
    caller), and we're now committing again. Cleanup must run so the prior
    graph data is removed before the new write.
    """
    service = _build_commit_service(in_memory_adapter)

    commit_data = {
        "entities": [{"name": "Alice", "type": "Person", "properties": {}}],
        "relationships": [],
        "suggested_templates": [],
    }
    # Cleanup-ordering test — disable orphan filter so Alice is written on both commits.
    file_info = {"id": seeded_source, "database_name": "default", "filtering_mode": "unfiltered"}

    await service.commit(file_id=seeded_source, commit_data=commit_data, file_info=file_info)

    in_memory_adapter.session.expire_all()
    from chaoscypher_core.adapters.sqlite.models import SourceRow

    row = in_memory_adapter.session.get(SourceRow, seeded_source)
    row.commit_complete = False
    row.status = SourceStatus.EXTRACTED
    in_memory_adapter.session.add(row)
    in_memory_adapter.session.commit()

    cleanup_calls: list[tuple[str, str]] = []
    original_cleanup = service._cleanup_previous_commit

    def spy_cleanup(file_id: str, source_id: str) -> dict[str, Any]:
        cleanup_calls.append((file_id, source_id))
        return original_cleanup(file_id, source_id)

    monkeypatch.setattr(service, "_cleanup_previous_commit", spy_cleanup)

    await service.commit(file_id=seeded_source, commit_data=commit_data, file_info=file_info)

    assert cleanup_calls == [(seeded_source, seeded_source)]


@pytest.mark.asyncio
async def test_commit_retries_on_db_lock(
    in_memory_adapter: SqliteAdapter,
    seeded_source: str,
) -> None:
    """If adapter.transaction() raises SQLITE_BUSY once, commit retries and succeeds.

    The retry wrapper re-runs the whole idempotent _commit_impl from scratch.
    Second attempt enters a real transaction and completes normally.
    """
    from sqlmodel import select

    from chaoscypher_core.adapters.sqlite.models import GraphNode, SourceRow

    service = _build_commit_service(in_memory_adapter)

    # Monkeypatch transaction() so the first entry raises a lock error,
    # then subsequent calls work normally via the real implementation.
    original_transaction = in_memory_adapter.transaction
    call_count = 0

    @contextmanager
    def flaky_transaction():  # type: ignore[misc]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OperationalError("", {}, Exception("database is locked"))
        with original_transaction():
            yield

    in_memory_adapter.transaction = flaky_transaction  # type: ignore[method-assign]

    commit_data = {
        "entities": [{"name": "Alice", "type": "Person", "properties": {}}],
        "relationships": [],
        "suggested_templates": [],
    }
    # Lock-retry test — disable orphan filter so Alice is written on the successful retry.
    result = await service.commit(
        file_id=seeded_source,
        commit_data=commit_data,
        file_info={"id": seeded_source, "database_name": "default", "filtering_mode": "unfiltered"},
    )

    # First call raised lock error; second succeeded
    assert call_count == 2
    # Commit completed — source row is committed
    in_memory_adapter.session.expire_all()
    row = in_memory_adapter.session.get(SourceRow, seeded_source)
    assert row.status == SourceStatus.COMMITTED
    assert row.commit_complete is True
    # Nodes were created on the successful retry
    nodes = list(in_memory_adapter.session.exec(select(GraphNode)))
    assert len(nodes) == 1
    assert len(result["created_nodes"]) == 1

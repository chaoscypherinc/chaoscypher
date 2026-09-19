# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The commit-time orphan filter must honour the source row's filtering choice.

The queued commit task's ``file_info`` is ``adapter.get_file(...)``, whose
narrow ``load_only`` projection omits ``filtering_mode`` and
``protect_orphans`` (recovery's rebuilt file_info carries neither either).
``_commit_impl`` used to read the mode from ``file_info`` alone, so it always
fell through to the engine default (``balanced``, orphans dropped) — a
``minimal`` / ``unfiltered`` source, or one whose row says
``protect_orphans=True``, lost its orphan entities at commit even though the
extraction finalizer honoured the row. These tests drive ``commit()`` with the
real ``get_file`` dict, exactly as ``_queue_commit_phase`` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlmodel import func, select

from chaoscypher_core.adapters.sqlite.models import GraphNode
from chaoscypher_core.models import SourceStatus


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


# Three entities, one relationship: ``Bob`` (index 2) is the orphan.
_COMMIT_DATA_WITH_ORPHAN: dict[str, Any] = {
    "entities": [
        {"name": "Alice", "type": "Person", "properties": {}},
        {"name": "Acme", "type": "Organization", "properties": {}},
        {"name": "Bob", "type": "Person", "properties": {}},
    ],
    "relationships": [
        {"source": 0, "target": 1, "type": "works_at"},
    ],
    "create_templates": False,
    "suggested_templates": [],
    "suggested_edge_templates": [],
}


def _build_commit_service(adapter: SqliteAdapter):
    """Construct SourceCommitService wired from the test adapter (house pattern)."""
    from chaoscypher_core.adapters.sqlite.engine import get_engine
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository, SearchRepository
    from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
    from chaoscypher_core.settings import EngineSettings

    graph_repository = GraphRepository(
        session=adapter.session,
        database_name=adapter.database_name,
    )
    search_repository = SearchRepository(
        engine=get_engine(adapter.db_path),
        vector_dim=4,
        embedding_model="test-model",
    )
    return SourceCommitService(
        graph_repository=graph_repository,
        source_repository=adapter,
        sources_repository=adapter,
        indexing_repository=adapter,
        search_repository=search_repository,
        settings=EngineSettings(),
    )


def _seed_extracted_source(adapter: SqliteAdapter, source_id: str, **row_overrides: Any) -> None:
    """Seed an EXTRACTED source row carrying the given filtering columns."""
    adapter.create_source(
        {
            "id": source_id,
            "database_name": adapter.database_name,
            "filename": f"{source_id}.md",
            "filepath": f"/tmp/{source_id}.md",
            "file_type": "markdown",
            "file_size": 42,
            "content_hash": f"hash-{source_id}",
            "status": SourceStatus.EXTRACTED.value,
            **row_overrides,
        }
    )
    adapter.complete_extraction(
        source_id=source_id,
        entities=[],
        relationships=[],
        forced_domain=None,
        detected_domain=None,
    )


async def _commit_with_real_file_info(adapter: SqliteAdapter, source_id: str) -> None:
    """Commit the way the finalizer does: ``file_info`` is the ``get_file`` projection."""
    file_info = adapter.get_file(source_id, adapter.database_name)
    assert file_info is not None
    # The premise of the defect: the projection carries neither column.
    assert "filtering_mode" not in file_info
    assert "protect_orphans" not in file_info
    await _build_commit_service(adapter).commit(
        file_id=source_id,
        commit_data=_COMMIT_DATA_WITH_ORPHAN,
        file_info=file_info,
    )


def _node_count(adapter: SqliteAdapter, source_id: str) -> int:
    assert adapter.session is not None
    return adapter.session.exec(
        select(func.count()).select_from(GraphNode).where(GraphNode.source_id == source_id)
    ).one()


def _orphans_filtered(adapter: SqliteAdapter, source_id: str) -> int:
    source = adapter.get_source(source_id, adapter.database_name)
    assert source is not None
    return int(source.get("orphan_entities_filtered") or 0)


@pytest.mark.asyncio
async def test_row_filtering_mode_protects_orphans_at_commit(
    adapter_with_default_templates: SqliteAdapter,
) -> None:
    """A ``minimal`` row keeps its orphan even though ``file_info`` never says so."""
    adapter = adapter_with_default_templates
    source_id = "src_minimal"
    _seed_extracted_source(adapter, source_id, filtering_mode="minimal")

    await _commit_with_real_file_info(adapter, source_id)

    assert _node_count(adapter, source_id) == 3
    assert _orphans_filtered(adapter, source_id) == 0


@pytest.mark.asyncio
async def test_row_protect_orphans_beats_preset_at_commit(
    adapter_with_default_templates: SqliteAdapter,
) -> None:
    """An explicit ``protect_orphans=True`` on the row wins over ``balanced``'s False."""
    adapter = adapter_with_default_templates
    source_id = "src_protected"
    _seed_extracted_source(adapter, source_id, filtering_mode="balanced", protect_orphans=True)

    await _commit_with_real_file_info(adapter, source_id)

    assert _node_count(adapter, source_id) == 3
    assert _orphans_filtered(adapter, source_id) == 0


@pytest.mark.asyncio
async def test_default_row_still_drops_orphans_at_commit(
    adapter_with_default_templates: SqliteAdapter,
) -> None:
    """Control: the default row (``balanced``, NULL override) keeps dropping orphans."""
    adapter = adapter_with_default_templates
    source_id = "src_default"
    _seed_extracted_source(adapter, source_id)

    await _commit_with_real_file_info(adapter, source_id)

    assert _node_count(adapter, source_id) == 2
    assert _orphans_filtered(adapter, source_id) == 1


@pytest.mark.asyncio
async def test_payload_filtering_mode_still_wins_over_row(
    adapter_with_default_templates: SqliteAdapter,
) -> None:
    """A caller-supplied ``file_info["filtering_mode"]`` keeps precedence over the row."""
    adapter = adapter_with_default_templates
    source_id = "src_payload"
    _seed_extracted_source(adapter, source_id, filtering_mode="minimal")

    file_info = adapter.get_file(source_id, adapter.database_name)
    assert file_info is not None
    await _build_commit_service(adapter).commit(
        file_id=source_id,
        commit_data=_COMMIT_DATA_WITH_ORPHAN,
        file_info={**file_info, "filtering_mode": "balanced"},
    )

    assert _node_count(adapter, source_id) == 2
    assert _orphans_filtered(adapter, source_id) == 1

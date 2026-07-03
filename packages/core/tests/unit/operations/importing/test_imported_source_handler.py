# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for the imported-entity search-indexing handlers."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.constants import (
    OP_INDEX_IMPORTED_NODES,
    OP_INDEX_IMPORTED_SOURCE,
    OPERATION_QUEUE_ROUTING,
    QUEUE_LLM,
)
from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.importing.imported_source_handler import (
    handle_index_imported_nodes,
    handle_index_imported_source,
)


def test_index_ops_routed_to_llm_queue() -> None:
    """Both index ops re-embed, so they must live on the LLM queue (CC044)."""
    assert OPERATION_QUEUE_ROUTING[OP_INDEX_IMPORTED_SOURCE] == QUEUE_LLM
    assert OPERATION_QUEUE_ROUTING[OP_INDEX_IMPORTED_NODES] == QUEUE_LLM


def _chunk_embedding_b64() -> str:
    vector = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    return base64.b64encode(vector.tobytes()).decode("ascii")


def _node(node_id: str, *, embedding=None) -> SimpleNamespace:
    return SimpleNamespace(id=node_id, label=f"label-{node_id}", properties={}, embedding=embedding)


def _service_with(*, nodes, vectors) -> tuple:
    """Build the (adapter, graph_repo, indexing_service, search_repo) mocks."""
    adapter = MagicMock()
    adapter.list_unembedded_chunks.return_value = [{"id": "c1", "content": "hello"}]
    # Content-free streaming fetch: (chunk_id, embedding) tuples only.
    adapter.iter_chunk_embeddings.return_value = [("c1", _chunk_embedding_b64())]

    graph_repo = MagicMock()
    graph_repo.list_nodes.return_value = nodes  # source path
    graph_repo.get_nodes_batch.return_value = nodes  # node-list path

    indexing_service = MagicMock()
    indexing_service.embed_chunks = AsyncMock(return_value=1)
    indexing_service.embedding_service.batch_embed = AsyncMock(
        return_value=SimpleNamespace(embeddings=vectors)
    )
    return adapter, graph_repo, indexing_service, MagicMock()


@pytest.mark.asyncio
async def test_handle_index_imported_source_embeds_then_indexes() -> None:
    """Happy path: nodes re-embedded + indexed, chunks embedded + vectors pushed."""
    dim = get_settings().search.vector_dimensions
    node = _node("n1", embedding=None)  # imported nodes arrive without a vector
    adapter, graph_repo, indexing_service, search_repo = _service_with(
        nodes=[node], vectors=[[0.1] * dim]
    )

    result = await handle_index_imported_source(
        data={"source_id": "src1"},
        source_repository=adapter,
        graph_repository=graph_repo,
        indexing_service=indexing_service,
        search_repository=search_repo,
        metadata={"database_name": "default"},
    )

    assert result["success"] is True
    assert result["nodes_embedded"] == 1
    assert result["nodes_indexed"] == 1
    assert result["chunks_embedded"] == 1
    assert result["chunks_indexed"] == 1

    # The node was re-embedded and persisted in ONE batched write (not N
    # per-node update_node calls) BEFORE indexing, so vec_search_nodes is fresh.
    indexing_service.embedding_service.batch_embed.assert_awaited_once()
    graph_repo.update_node_embeddings_batch.assert_called_once_with({"n1": [0.1] * dim})
    graph_repo.update_node.assert_not_called()
    assert node.embedding == [0.1] * dim
    search_repo.index_nodes_batch.assert_called_once()
    # Chunks embedded + chunk vectors pushed (content-free) with item_type="chunk".
    indexing_service.embed_chunks.assert_awaited_once()
    adapter.iter_chunk_embeddings.assert_called_once()
    search_repo.index_embeddings_batch.assert_called_once()
    _, kwargs = search_repo.index_embeddings_batch.call_args
    assert kwargs["item_type"] == "chunk"
    assert adapter.update_source_columns.called


@pytest.mark.asyncio
async def test_nodes_with_valid_vectors_are_not_reembedded() -> None:
    """Idempotent re-run: a node that already has a right-dim vector is skipped."""
    dim = get_settings().search.vector_dimensions
    node = _node("n1", embedding=[0.5] * dim)  # already embedded (e.g. a retry)
    adapter, graph_repo, indexing_service, search_repo = _service_with(
        nodes=[node], vectors=[[0.1] * dim]
    )

    result = await handle_index_imported_source(
        data={"source_id": "src1"},
        source_repository=adapter,
        graph_repository=graph_repo,
        indexing_service=indexing_service,
        search_repository=search_repo,
        metadata={"database_name": "default"},
    )

    assert result["nodes_embedded"] == 0
    indexing_service.embedding_service.batch_embed.assert_not_awaited()
    graph_repo.update_node_embeddings_batch.assert_not_called()
    assert node.embedding == [0.5] * dim  # untouched
    search_repo.index_nodes_batch.assert_called_once()  # still indexed
    assert adapter.update_source_columns.called


@pytest.mark.asyncio
async def test_handle_index_imported_source_requires_source_id() -> None:
    """A missing/non-string source_id is a ValidationError on ``source_id``."""
    with pytest.raises(ValidationError) as exc_info:
        await handle_index_imported_source(
            data={},
            source_repository=MagicMock(),
            graph_repository=MagicMock(),
            indexing_service=MagicMock(),
            search_repository=MagicMock(),
        )
    assert exc_info.value.field == "source_id"


@pytest.mark.asyncio
async def test_handle_index_imported_source_degrades_without_search_repo() -> None:
    """No search repository → degrade (don't claim 'indexed'), don't embed."""
    indexing_service = MagicMock()
    indexing_service.embed_chunks = AsyncMock()

    result = await handle_index_imported_source(
        data={"source_id": "src1"},
        source_repository=MagicMock(),
        graph_repository=MagicMock(),
        indexing_service=indexing_service,
        search_repository=None,
        metadata={"database_name": "default"},
    )

    assert result["success"] is False
    assert result["reason"] == "no_search_repository"
    indexing_service.embed_chunks.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_index_imported_nodes — knowledge-only (lexicon / CLI) path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_index_imported_nodes_reembeds_and_indexes() -> None:
    """Node-list path: nodes fetched by id, re-embedded, and indexed (no chunks)."""
    dim = get_settings().search.vector_dimensions
    node = _node("n1", embedding=None)
    adapter, graph_repo, indexing_service, search_repo = _service_with(
        nodes=[node], vectors=[[0.2] * dim]
    )

    result = await handle_index_imported_nodes(
        data={"node_ids": ["n1"]},
        source_repository=adapter,
        graph_repository=graph_repo,
        indexing_service=indexing_service,
        search_repository=search_repo,
        metadata={"database_name": "default"},
    )

    assert result["success"] is True
    assert result["nodes_indexed"] == 1
    assert result["nodes_embedded"] == 1
    graph_repo.get_nodes_batch.assert_called_once_with(["n1"])
    graph_repo.update_node_embeddings_batch.assert_called_once_with({"n1": [0.2] * dim})
    search_repo.index_nodes_batch.assert_called_once()
    # Knowledge-only: no chunk work, no source-status write.
    indexing_service.embed_chunks.assert_not_awaited()
    adapter.update_source_columns.assert_not_called()


@pytest.mark.asyncio
async def test_handle_index_imported_nodes_requires_node_ids() -> None:
    """A missing/non-list node_ids is a ValidationError on ``node_ids``."""
    with pytest.raises(ValidationError) as exc_info:
        await handle_index_imported_nodes(
            data={"node_ids": "n1"},
            source_repository=MagicMock(),
            graph_repository=MagicMock(),
            indexing_service=MagicMock(),
            search_repository=MagicMock(),
        )
    assert exc_info.value.field == "node_ids"


@pytest.mark.asyncio
async def test_handle_index_imported_nodes_empty_is_noop_success() -> None:
    """An empty node list is a no-op success (nothing to index)."""
    graph_repo = MagicMock()
    result = await handle_index_imported_nodes(
        data={"node_ids": []},
        source_repository=MagicMock(),
        graph_repository=graph_repo,
        indexing_service=MagicMock(),
        search_repository=MagicMock(),
        metadata={"database_name": "default"},
    )
    assert result == {"success": True, "nodes_indexed": 0, "nodes_embedded": 0}
    graph_repo.get_nodes_batch.assert_not_called()

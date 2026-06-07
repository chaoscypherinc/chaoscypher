# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for NodeToolHandlers.

Covers search_nodes, search_chunks, get_node, create_node, update_node,
delete_node, get_node_context, and resolve_node with mocked repositories
and callbacks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.services.workflows.tools.engine.handlers.node_handlers import (
    NodeToolHandlers,
)
from chaoscypher_core.settings import SearchSettings


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

_CHUNK_HYDRATION_MODULE = "chaoscypher_core.services.workflows.tools.engine.handlers.node_handlers"


def make_node(
    nid: str = "n1",
    label: str = "Alice",
    template_id: str = "person",
    properties: dict[str, Any] | None = None,
    source_id: str | None = "src1",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    embedding: list[float] | None = None,
) -> SimpleNamespace:
    """Create a minimal node object for mocking graph repository returns.

    Args:
        nid: Node identifier.
        label: Human-readable label.
        template_id: Template / type identifier.
        properties: Node properties dict.
        source_id: Owning source identifier.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        embedding: Optional embedding vector.

    Returns:
        A ``SimpleNamespace`` mimicking a node entity.

    """
    return SimpleNamespace(
        id=nid,
        label=label,
        template_id=template_id,
        properties=properties or {},
        source_id=source_id,
        created_at=created_at or datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=updated_at or datetime(2024, 1, 2, tzinfo=UTC),
        embedding=embedding or [0.1, 0.2, 0.3],
    )


def make_edge(
    eid: str,
    source_id: str,
    target_id: str,
    label: str = "related_to",
    template_id: str = "default",
) -> SimpleNamespace:
    """Create a minimal edge object for mocking graph repository returns.

    Args:
        eid: Edge identifier.
        source_id: Source node identifier.
        target_id: Target node identifier.
        label: Relationship label.
        template_id: Template / type identifier.

    Returns:
        A ``SimpleNamespace`` mimicking an edge entity.

    """
    return SimpleNamespace(
        id=eid,
        source_node_id=source_id,
        target_node_id=target_id,
        label=label,
        template_id=template_id,
    )


def make_chunk(
    chunk_id: str = "chunk1",
    content: str = "Test content.",
    source_id: str = "src1",
    database_name: str = "default",
    chunk_index: int = 0,
    page_number: int = 1,
    section: str | None = None,
    chunk_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a minimal chunk dict matching the storage protocol output.

    Args:
        chunk_id: Chunk identifier.
        content: Text content of the chunk.
        source_id: Owning source identifier.
        database_name: Database name.
        chunk_index: Chunk ordinal index.
        page_number: Page number.
        section: Optional section heading.
        chunk_metadata: Optional metadata dict.

    Returns:
        A dict matching the shape returned by indexing storage protocols.

    """
    return {
        "id": chunk_id,
        "content": content,
        "source_id": source_id,
        "database_name": database_name,
        "chunk_index": chunk_index,
        "page_number": page_number,
        "section": section,
        "chunk_metadata": chunk_metadata,
    }


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_repo() -> MagicMock:
    """Mock graph repository with all required methods."""
    repo = MagicMock()
    repo.get_node.return_value = None
    repo.get_nodes_batch.return_value = []
    repo.get_template.return_value = None
    repo.create_node.return_value = None
    repo.update_node.return_value = None
    repo.delete_node.return_value = False
    repo.list_edges.return_value = []
    return repo


@pytest.fixture
def search_repo() -> MagicMock:
    """Mock search repository with async hybrid_search and sync keyword_search."""
    repo = MagicMock()
    repo.hybrid_search = AsyncMock(return_value=[])
    repo.keyword_search = MagicMock(return_value=[])
    repo.vector_search = MagicMock(return_value=[])
    return repo


@pytest.fixture
def indexing_repo() -> MagicMock:
    """Mock indexing repository."""
    repo = MagicMock()
    repo.get_chunk_by_id.return_value = None
    repo.get_source.return_value = None
    return repo


@pytest.fixture
def embedding_callback() -> AsyncMock:
    """Async embedding callback returning a simple vector."""
    cb = AsyncMock()
    cb.return_value = SimpleNamespace(embedding=[0.1, 0.2, 0.3])
    return cb


@pytest.fixture
def search_settings() -> SearchSettings:
    """Provide default SearchSettings with reranking disabled for simplicity."""
    return SearchSettings(enable_rerank=False)


def _make_handler(
    graph_repo: MagicMock,
    search_repo: MagicMock,
    indexing_repo: MagicMock | None = None,
    embedding_callback: AsyncMock | None = None,
    search_settings: SearchSettings | None = None,
) -> NodeToolHandlers:
    """Construct a ``NodeToolHandlers`` instance from mocks.

    Args:
        graph_repo: Mock graph repository.
        search_repo: Mock search repository.
        indexing_repo: Optional mock indexing repository.
        embedding_callback: Optional async embedding callback.
        search_settings: Optional search settings.

    Returns:
        Configured handler ready for testing.

    """
    return NodeToolHandlers(
        graph_repository=graph_repo,
        search_repository=search_repo,
        indexing_repository=indexing_repo,
        embedding_callback=embedding_callback,
        search_settings=search_settings,
    )


# ===========================================================================
# search_nodes tests
# ===========================================================================


class TestSearchNodes:
    """Tests for the search_nodes method."""

    @pytest.mark.asyncio
    async def test_basic_search_returns_nodes(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Basic search returns matching nodes with scores."""
        node = make_node("n1", "Alice", "person")
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.9)])
        graph_repo.get_nodes_batch.return_value = [node]

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.search_nodes("Alice")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["nodes"][0]["id"] == "n1"
        assert result["nodes"][0]["label"] == "Alice"
        assert result["nodes"][0]["score"] == 0.9
        assert result["query"] == "Alice"
        search_repo.hybrid_search.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_separates_chunk_and_node_ids(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Chunk-prefixed IDs are separated from node IDs in search results."""
        node = make_node("n1", "Alice")
        search_repo.hybrid_search = AsyncMock(
            return_value=[("n1", 0.9), ("chunk:abc123", 0.85), ("chunk:def456", 0.7)]
        )
        graph_repo.get_nodes_batch.return_value = [node]

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.search_nodes("Alice")

        assert result["count"] == 1
        assert result["chunks_found"] == 2
        assert result["nodes"][0]["id"] == "n1"

    @pytest.mark.asyncio
    async def test_template_filter_by_template_id(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Template filter matches on template_id (case-insensitive partial match)."""
        person_node = make_node("n1", "Alice", "person")
        concept_node = make_node("n2", "Knowledge", "concept")
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.9), ("n2", 0.85)])
        graph_repo.get_nodes_batch.return_value = [person_node, concept_node]
        # Template lookup returns name
        graph_repo.get_template.side_effect = lambda tid: SimpleNamespace(name=tid.title())

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.search_nodes("test", template_ids=["person"])

        assert result["count"] == 1
        assert result["nodes"][0]["id"] == "n1"
        assert result["filtered_out"] == 1
        assert result["template_filter"] == ["person"]

    @pytest.mark.asyncio
    async def test_template_filter_by_template_name(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Template filter matches on template name (case-insensitive partial)."""
        node = make_node("n1", "Alice", "tmpl_person_v2")
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.9)])
        graph_repo.get_nodes_batch.return_value = [node]
        # Template has a human-readable name
        graph_repo.get_template.return_value = SimpleNamespace(name="Person Template")

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.search_nodes("test", template_ids=["person"])

        # "person" is in the template name "Person Template" (case-insensitive)
        assert result["count"] == 1
        assert result["nodes"][0]["id"] == "n1"

    @pytest.mark.asyncio
    async def test_source_scope_filters_nodes(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Source scope filters nodes not in the allowed source_ids."""
        allowed_node = make_node("n1", "Alice", source_id="src_allowed")
        blocked_node = make_node("n2", "Bob", source_id="src_blocked")
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.9), ("n2", 0.85)])
        graph_repo.get_nodes_batch.return_value = [allowed_node, blocked_node]

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.search_nodes("test", source_ids=["src_allowed"])

        assert result["count"] == 1
        assert result["nodes"][0]["id"] == "n1"

    @pytest.mark.asyncio
    async def test_empty_results(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Empty hybrid_search results return empty node list."""
        search_repo.hybrid_search = AsyncMock(return_value=[])
        graph_repo.get_nodes_batch.return_value = []

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.search_nodes("nothing")

        assert result["success"] is True
        assert result["count"] == 0
        assert result["nodes"] == []
        assert result["chunks_found"] == 0

    @pytest.mark.asyncio
    async def test_hint_generated_for_abstract_concepts(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Hint is generated when results are abstract concepts, not persons."""
        node = make_node("n1", "Freedom", "concept")
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.9)])
        graph_repo.get_nodes_batch.return_value = [node]

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.search_nodes("freedom")

        assert result["hint"] is not None
        assert "template_ids" in result["hint"]

    @pytest.mark.asyncio
    async def test_no_hint_for_person_results(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """No hint is generated when results include person-like templates."""
        node = make_node("n1", "Alice", "person")
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.9)])
        graph_repo.get_nodes_batch.return_value = [node]

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.search_nodes("Alice")

        assert result["hint"] is None

    @pytest.mark.asyncio
    async def test_no_hint_when_template_filter_active(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """No hint is generated when template_ids filter is applied."""
        node = make_node("n1", "Freedom", "concept")
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.9)])
        graph_repo.get_nodes_batch.return_value = [node]
        graph_repo.get_template.return_value = SimpleNamespace(name="Concept")

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.search_nodes("freedom", template_ids=["concept"])

        assert result["hint"] is None

    @pytest.mark.asyncio
    async def test_result_types_populated(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Result types dict is populated with template type counts."""
        nodes = [
            make_node("n1", "Alice", "person"),
            make_node("n2", "Bob", "person"),
            make_node("n3", "Freedom", "concept"),
        ]
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.9), ("n2", 0.85), ("n3", 0.8)])
        graph_repo.get_nodes_batch.return_value = nodes

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.search_nodes("test")

        assert result["result_types"]["person"] == 2
        assert result["result_types"]["concept"] == 1

    @pytest.mark.asyncio
    async def test_uses_embedding_callback(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """hybrid_search is called with the embedding callback."""
        search_repo.hybrid_search = AsyncMock(return_value=[])
        graph_repo.get_nodes_batch.return_value = []

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        await handler.search_nodes("test")

        call_kwargs = search_repo.hybrid_search.call_args.kwargs
        assert call_kwargs["embedding_provider_callback"] is embedding_callback

    @pytest.mark.asyncio
    async def test_respects_limit_parameter(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Limit parameter is forwarded to hybrid_search as k."""
        search_repo.hybrid_search = AsyncMock(return_value=[])
        graph_repo.get_nodes_batch.return_value = []

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        await handler.search_nodes("test", limit=25)

        call_kwargs = search_repo.hybrid_search.call_args.kwargs
        assert call_kwargs["k"] == 25


# ===========================================================================
# search_chunks tests
# ===========================================================================


class TestSearchChunks:
    """Tests for the search_chunks method."""

    @pytest.mark.asyncio
    async def test_no_indexing_repo_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when no indexing repository is available."""
        handler = _make_handler(graph_repo, search_repo, indexing_repo=None)
        result = await handler.search_chunks("test")

        assert result["success"] is False
        assert "not available" in result["error"]

    @pytest.mark.asyncio
    async def test_basic_chunk_search(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        search_settings: SearchSettings,
    ) -> None:
        """Basic chunk search returns hydrated chunk data."""
        chunk = make_chunk("c1", "Alice went to the market.")
        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:c1", 0.9)])
        indexing_repo.get_chunk_by_id.return_value = chunk

        with (
            patch(f"{_CHUNK_HYDRATION_MODULE}.format_chunk_content") as mock_format,
            patch(f"{_CHUNK_HYDRATION_MODULE}.clean_chunk_metadata") as mock_clean,
        ):
            mock_format.return_value = ("[CHUNK C0 | ]\n[S1] Alice went to the market.", 1)
            mock_clean.return_value = None

            handler = _make_handler(
                graph_repo,
                search_repo,
                indexing_repo=indexing_repo,
                embedding_callback=embedding_callback,
                search_settings=search_settings,
            )
            result = await handler.search_chunks("Alice")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["chunks"][0]["chunk_id"] == "c1"
        assert result["chunks"][0]["chunk_alias"] == "C0"
        assert result["chunks"][0]["source_id"] == "src1"
        assert result["query"] == "Alice"

    @pytest.mark.asyncio
    async def test_source_id_filter(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        search_settings: SearchSettings,
    ) -> None:
        """Single source_id filter excludes chunks from other sources."""
        allowed_chunk = make_chunk("c1", "Allowed content.", source_id="src_allowed")
        blocked_chunk = make_chunk("c2", "Blocked content.", source_id="src_blocked")

        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:c1", 0.9), ("chunk:c2", 0.85)])

        def chunk_side_effect(chunk_id: str) -> dict[str, Any] | None:
            if chunk_id == "c1":
                return allowed_chunk
            if chunk_id == "c2":
                return blocked_chunk
            return None

        indexing_repo.get_chunk_by_id.side_effect = chunk_side_effect

        with (
            patch(f"{_CHUNK_HYDRATION_MODULE}.format_chunk_content") as mock_format,
            patch(f"{_CHUNK_HYDRATION_MODULE}.clean_chunk_metadata") as mock_clean,
        ):
            mock_format.return_value = ("[CHUNK C0 | ]\n[S1] Allowed content.", 1)
            mock_clean.return_value = None

            handler = _make_handler(
                graph_repo,
                search_repo,
                indexing_repo=indexing_repo,
                embedding_callback=embedding_callback,
                search_settings=search_settings,
            )
            result = await handler.search_chunks("test", source_id="src_allowed")

        assert result["count"] == 1
        assert result["chunks"][0]["chunk_id"] == "c1"

    @pytest.mark.asyncio
    async def test_source_ids_filter(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        search_settings: SearchSettings,
    ) -> None:
        """source_ids list filter excludes chunks from other sources."""
        allowed_chunk = make_chunk("c1", "Content.", source_id="src_ok")
        blocked_chunk = make_chunk("c2", "Blocked.", source_id="src_no")

        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:c1", 0.9), ("chunk:c2", 0.85)])

        def chunk_side_effect(chunk_id: str) -> dict[str, Any] | None:
            if chunk_id == "c1":
                return allowed_chunk
            if chunk_id == "c2":
                return blocked_chunk
            return None

        indexing_repo.get_chunk_by_id.side_effect = chunk_side_effect

        with (
            patch(f"{_CHUNK_HYDRATION_MODULE}.format_chunk_content") as mock_format,
            patch(f"{_CHUNK_HYDRATION_MODULE}.clean_chunk_metadata") as mock_clean,
        ):
            mock_format.return_value = ("[CHUNK C0 | ]\n[S1] Content.", 1)
            mock_clean.return_value = None

            handler = _make_handler(
                graph_repo,
                search_repo,
                indexing_repo=indexing_repo,
                embedding_callback=embedding_callback,
                search_settings=search_settings,
            )
            result = await handler.search_chunks("test", source_ids=["src_ok"])

        assert result["count"] == 1
        assert result["chunks"][0]["chunk_id"] == "c1"

    @pytest.mark.asyncio
    async def test_empty_chunk_results(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        search_settings: SearchSettings,
    ) -> None:
        """Empty search results return empty chunks list."""
        search_repo.hybrid_search = AsyncMock(return_value=[])

        handler = _make_handler(
            graph_repo,
            search_repo,
            indexing_repo=indexing_repo,
            embedding_callback=embedding_callback,
            search_settings=search_settings,
        )
        result = await handler.search_chunks("nothing")

        assert result["success"] is True
        assert result["count"] == 0
        assert result["chunks"] == []

    @pytest.mark.asyncio
    async def test_node_ids_ignored_in_chunk_search(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        search_settings: SearchSettings,
    ) -> None:
        """Non-chunk search results (node IDs) are filtered out."""
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.95), ("n2", 0.9)])

        handler = _make_handler(
            graph_repo,
            search_repo,
            indexing_repo=indexing_repo,
            embedding_callback=embedding_callback,
            search_settings=search_settings,
        )
        result = await handler.search_chunks("test")

        assert result["count"] == 0
        assert result["chunks"] == []

    @pytest.mark.asyncio
    async def test_uses_low_similarity_threshold(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        search_settings: SearchSettings,
    ) -> None:
        """Chunk search uses min_similarity=0.3 for broader retrieval."""
        search_repo.hybrid_search = AsyncMock(return_value=[])

        handler = _make_handler(
            graph_repo,
            search_repo,
            indexing_repo=indexing_repo,
            embedding_callback=embedding_callback,
            search_settings=search_settings,
        )
        await handler.search_chunks("test")

        call_kwargs = search_repo.hybrid_search.call_args.kwargs
        assert call_kwargs["min_similarity"] == 0.3

    @pytest.mark.asyncio
    async def test_chunk_metadata_cleaned(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        search_settings: SearchSettings,
    ) -> None:
        """Chunk metadata is cleaned via clean_chunk_metadata."""
        raw_meta = {
            "hierarchical_group": {
                "combined_content": "should be stripped",
                "group_id": "g1",
            }
        }
        chunk = make_chunk("c1", "Content.", chunk_metadata=raw_meta)
        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:c1", 0.9)])
        indexing_repo.get_chunk_by_id.return_value = chunk

        with (
            patch(f"{_CHUNK_HYDRATION_MODULE}.format_chunk_content") as mock_format,
            patch(f"{_CHUNK_HYDRATION_MODULE}.clean_chunk_metadata") as mock_clean,
        ):
            mock_format.return_value = ("[CHUNK C0 | ]\n[S1] Content.", 1)
            mock_clean.return_value = {"hierarchical_group": {"group_id": "g1"}}

            handler = _make_handler(
                graph_repo,
                search_repo,
                indexing_repo=indexing_repo,
                embedding_callback=embedding_callback,
                search_settings=search_settings,
            )
            result = await handler.search_chunks("test")

        mock_clean.assert_called_once_with(raw_meta)
        assert result["chunks"][0]["chunk_metadata"] == {"hierarchical_group": {"group_id": "g1"}}

    @pytest.mark.asyncio
    async def test_chunk_alias_sequential(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        search_settings: SearchSettings,
    ) -> None:
        """Chunk aliases are assigned sequentially as C0, C1, etc."""
        c1 = make_chunk("c1", "First.", source_id="src1")
        c2 = make_chunk("c2", "Second.", source_id="src1")

        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:c1", 0.9), ("chunk:c2", 0.85)])

        def chunk_side_effect(chunk_id: str) -> dict[str, Any] | None:
            if chunk_id == "c1":
                return c1
            if chunk_id == "c2":
                return c2
            return None

        indexing_repo.get_chunk_by_id.side_effect = chunk_side_effect

        with (
            patch(f"{_CHUNK_HYDRATION_MODULE}.format_chunk_content") as mock_format,
            patch(f"{_CHUNK_HYDRATION_MODULE}.clean_chunk_metadata") as mock_clean,
        ):
            mock_format.side_effect = [
                ("[CHUNK C0 | ]\n[S1] First.", 1),
                ("[CHUNK C1 | ]\n[S1] Second.", 1),
            ]
            mock_clean.return_value = None

            handler = _make_handler(
                graph_repo,
                search_repo,
                indexing_repo=indexing_repo,
                embedding_callback=embedding_callback,
                search_settings=search_settings,
            )
            result = await handler.search_chunks("test", limit=10)

        assert result["chunks"][0]["chunk_alias"] == "C0"
        assert result["chunks"][1]["chunk_alias"] == "C1"

    @pytest.mark.asyncio
    async def test_min_limit_enforced(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Minimum limit from search_settings is enforced when limit is too low."""
        settings = SearchSettings(enable_rerank=False, search_chunks_min_limit=10)
        search_repo.hybrid_search = AsyncMock(return_value=[])

        handler = _make_handler(
            graph_repo,
            search_repo,
            indexing_repo=indexing_repo,
            embedding_callback=embedding_callback,
            search_settings=settings,
        )
        await handler.search_chunks("test", limit=2)

        # The k passed to hybrid_search should reflect the enforced min
        call_kwargs = search_repo.hybrid_search.call_args.kwargs
        # limit=max(2,10)=10, fetch_count=max(10*2, 15)=20
        assert call_kwargs["k"] == 20

    @pytest.mark.asyncio
    async def test_source_filename_resolved(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        search_settings: SearchSettings,
    ) -> None:
        """Source filename is resolved via indexing.get_source for citations."""
        chunk = make_chunk("c1", "Content.", source_id="src1")
        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:c1", 0.9)])
        indexing_repo.get_chunk_by_id.return_value = chunk
        indexing_repo.get_source.return_value = {"filename": "document.pdf"}

        with (
            patch(f"{_CHUNK_HYDRATION_MODULE}.format_chunk_content") as mock_format,
            patch(f"{_CHUNK_HYDRATION_MODULE}.clean_chunk_metadata") as mock_clean,
        ):
            mock_format.return_value = ("[CHUNK C0 | document.pdf]\n[S1] Content.", 1)
            mock_clean.return_value = None

            handler = _make_handler(
                graph_repo,
                search_repo,
                indexing_repo=indexing_repo,
                embedding_callback=embedding_callback,
                search_settings=search_settings,
            )
            result = await handler.search_chunks("test")

        assert result["chunks"][0]["filename"] == "document.pdf"
        indexing_repo.get_source.assert_called_once()


# ===========================================================================
# get_node tests
# ===========================================================================


class TestGetNode:
    """Tests for the get_node method."""

    @pytest.mark.asyncio
    async def test_get_by_id(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Retrieves a node by its ID."""
        node = make_node("n1", "Alice")
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node(node_id="n1")

        assert result["success"] is True
        assert result["node"]["id"] == "n1"
        assert result["node"]["label"] == "Alice"
        assert result["node"]["template_id"] == "person"
        assert result["node"]["source_id"] == "src1"
        assert result["search_query"] is None

    @pytest.mark.asyncio
    async def test_get_by_query(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Finds a node via keyword search when query is provided without node_id."""
        node = make_node("n1", "Alice")
        search_repo.keyword_search.return_value = [("n1", 5.0)]
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node(query="Alice")

        assert result["success"] is True
        assert result["node"]["id"] == "n1"
        assert result["search_query"] == "Alice"
        search_repo.keyword_search.assert_called_once_with("Alice", limit=1)

    @pytest.mark.asyncio
    async def test_get_by_query_no_results(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when query search finds no nodes."""
        search_repo.keyword_search.return_value = []

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node(query="nonexistent")

        assert result["success"] is False
        assert "No nodes found" in result["error"]

    @pytest.mark.asyncio
    async def test_not_found(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when node_id does not exist."""
        graph_repo.get_node.return_value = None

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node(node_id="missing")

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_both_missing_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when neither node_id nor query is provided."""
        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node()

        assert result["success"] is False
        assert "Either node_id or query" in result["error"]

    @pytest.mark.asyncio
    async def test_source_scope_check(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when node is out of source scope."""
        node = make_node("n1", "Alice", source_id="other_src")
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node(node_id="n1", source_ids=["allowed_src"])

        assert result["success"] is False
        assert "not accessible" in result["error"]

    @pytest.mark.asyncio
    async def test_datetime_converted_to_iso(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Datetime fields are converted to ISO format strings."""
        created = datetime(2024, 3, 15, 10, 30, 0, tzinfo=UTC)
        updated = datetime(2024, 6, 20, 14, 45, 0, tzinfo=UTC)
        node = make_node("n1", "Alice", created_at=created, updated_at=updated)
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node(node_id="n1")

        assert result["node"]["created_at"] == "2024-03-15T10:30:00+00:00"
        assert result["node"]["updated_at"] == "2024-06-20T14:45:00+00:00"

    @pytest.mark.asyncio
    async def test_none_datetimes_handled(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """None datetimes result in None in the output."""
        node = make_node("n1", "Alice", created_at=None, updated_at=None)
        # Override the defaults set by make_node
        node.created_at = None
        node.updated_at = None
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node(node_id="n1")

        assert result["node"]["created_at"] is None
        assert result["node"]["updated_at"] is None


# ===========================================================================
# create_node tests
# ===========================================================================


class TestCreateNode:
    """Tests for the create_node method."""

    @pytest.mark.asyncio
    async def test_basic_creation(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Creates a node and returns success with node details."""
        created_node = make_node("n_new", "New Node", "person", properties={"age": 25})
        graph_repo.create_node.return_value = created_node

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.create_node(
            template_id="person", label="New Node", properties={"age": 25}
        )

        assert result["success"] is True
        assert result["node_id"] == "n_new"
        assert result["node"]["label"] == "New Node"
        assert result["node"]["template_id"] == "person"
        assert result["node"]["properties"] == {"age": 25}
        assert "Created node" in result["message"]

    @pytest.mark.asyncio
    async def test_create_with_empty_properties(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Creates a node with empty properties dict."""
        created_node = make_node("n_new", "Simple Node", "thing", properties={})
        graph_repo.create_node.return_value = created_node

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.create_node(template_id="thing", label="Simple Node", properties={})

        assert result["success"] is True
        assert result["node"]["properties"] == {}

    @pytest.mark.asyncio
    async def test_create_exception_caught_by_decorator(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Exception during creation is caught by @tool_handler decorator."""
        graph_repo.create_node.side_effect = RuntimeError("DB error")

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.create_node(template_id="person", label="Fail", properties={})

        assert result["success"] is False
        assert "Operation failed" in result["error"]


# ===========================================================================
# update_node tests
# ===========================================================================


class TestUpdateNode:
    """Tests for the update_node method."""

    @pytest.mark.asyncio
    async def test_update_label(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Updates only the label of an existing node."""
        updated = make_node("n1", "Alice Updated")
        graph_repo.update_node.return_value = updated

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.update_node(node_id="n1", label="Alice Updated")

        assert result["success"] is True
        assert result["node_id"] == "n1"
        # Verify NodeUpdate was called with only label
        call_args = graph_repo.update_node.call_args
        update_obj = call_args[0][1]
        assert update_obj.label == "Alice Updated"
        assert update_obj.properties is None

    @pytest.mark.asyncio
    async def test_update_properties(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Updates only the properties of an existing node."""
        updated = make_node("n1", "Alice", properties={"age": 31})
        graph_repo.update_node.return_value = updated

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.update_node(node_id="n1", properties={"age": 31})

        assert result["success"] is True
        call_args = graph_repo.update_node.call_args
        update_obj = call_args[0][1]
        assert update_obj.label is None
        assert update_obj.properties == {"age": 31}

    @pytest.mark.asyncio
    async def test_update_both_label_and_properties(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Updates both label and properties simultaneously."""
        updated = make_node("n1", "Alice Renamed", properties={"age": 31})
        graph_repo.update_node.return_value = updated

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.update_node(
            node_id="n1", label="Alice Renamed", properties={"age": 31}
        )

        assert result["success"] is True
        call_args = graph_repo.update_node.call_args
        update_obj = call_args[0][1]
        assert update_obj.label == "Alice Renamed"
        assert update_obj.properties == {"age": 31}

    @pytest.mark.asyncio
    async def test_update_fails_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when graph.update_node returns None."""
        graph_repo.update_node.return_value = None

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.update_node(node_id="n1", label="Fail")

        assert result["success"] is False
        assert "Failed to update" in result["error"]

    @pytest.mark.asyncio
    async def test_update_scope_check(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when node is out of source scope before update."""
        existing = make_node("n1", "Alice", source_id="other_src")
        graph_repo.get_node.return_value = existing

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.update_node(node_id="n1", label="Hacked", source_ids=["allowed_src"])

        assert result["success"] is False
        assert "not accessible" in result["error"]
        # update_node should NOT have been called
        graph_repo.update_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_scope_check_passes(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Allows update when node is within source scope."""
        existing = make_node("n1", "Alice", source_id="allowed_src")
        updated = make_node("n1", "Alice Updated", source_id="allowed_src")
        graph_repo.get_node.return_value = existing
        graph_repo.update_node.return_value = updated

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.update_node(
            node_id="n1", label="Alice Updated", source_ids=["allowed_src"]
        )

        assert result["success"] is True
        graph_repo.update_node.assert_called_once()


# ===========================================================================
# delete_node tests
# ===========================================================================


class TestDeleteNode:
    """Tests for the delete_node method."""

    @pytest.mark.asyncio
    async def test_delete_success(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns success when graph.delete_node returns True."""
        graph_repo.delete_node.return_value = True

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.delete_node(node_id="n1")

        assert result["success"] is True
        assert "Deleted" in result["message"]
        graph_repo.delete_node.assert_called_once_with("n1")

    @pytest.mark.asyncio
    async def test_delete_failure(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when graph.delete_node returns False."""
        graph_repo.delete_node.return_value = False

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.delete_node(node_id="n1")

        assert result["success"] is False
        assert "Failed to delete" in result["error"]

    @pytest.mark.asyncio
    async def test_delete_scope_check(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when node is out of source scope before deletion."""
        existing = make_node("n1", "Alice", source_id="other_src")
        graph_repo.get_node.return_value = existing

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.delete_node(node_id="n1", source_ids=["allowed_src"])

        assert result["success"] is False
        assert "not accessible" in result["error"]
        graph_repo.delete_node.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_scope_check_passes(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Allows deletion when node is within source scope."""
        existing = make_node("n1", "Alice", source_id="allowed_src")
        graph_repo.get_node.return_value = existing
        graph_repo.delete_node.return_value = True

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.delete_node(node_id="n1", source_ids=["allowed_src"])

        assert result["success"] is True
        graph_repo.delete_node.assert_called_once_with("n1")

    @pytest.mark.asyncio
    async def test_delete_no_scope_skips_check(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Without source_ids, scope check is skipped."""
        graph_repo.delete_node.return_value = True

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.delete_node(node_id="n1")

        assert result["success"] is True
        # get_node should not be called when source_ids is None
        graph_repo.get_node.assert_not_called()


# ===========================================================================
# get_node_context tests
# ===========================================================================


class TestGetNodeContext:
    """Tests for the get_node_context method."""

    @pytest.mark.asyncio
    async def test_context_with_edges(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns node context with outgoing and incoming edges."""
        main_node = make_node("n1", "Alice")
        related_node = make_node("n2", "Bob")
        incoming_node = make_node("n3", "Charlie")

        graph_repo.get_node.return_value = main_node

        out_edge = make_edge("e1", "n1", "n2", label="knows")
        in_edge = make_edge("e2", "n3", "n1", label="follows")

        graph_repo.list_edges.side_effect = lambda **kwargs: (
            [out_edge] if kwargs.get("source_node_id") == "n1" else [in_edge]
        )
        graph_repo.get_nodes_batch.return_value = [related_node, incoming_node]

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node_context(node_id="n1")

        assert result["success"] is True
        assert result["node"]["id"] == "n1"
        assert len(result["outgoing_edges"]) == 1
        assert result["outgoing_edges"][0]["label"] == "knows"
        assert result["outgoing_edges"][0]["target"]["label"] == "Bob"
        assert len(result["incoming_edges"]) == 1
        assert result["incoming_edges"][0]["label"] == "follows"
        assert result["incoming_edges"][0]["source"]["label"] == "Charlie"
        assert result["edge_summary"]["outgoing_count"] == 1
        assert result["edge_summary"]["incoming_count"] == 1
        assert result["edge_summary"]["total_related_nodes"] == 2

    @pytest.mark.asyncio
    async def test_context_without_edges(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns node context without edges when include_edges is False."""
        node = make_node("n1", "Alice")
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node_context(node_id="n1", include_edges=False)

        assert result["success"] is True
        assert result["node"]["id"] == "n1"
        assert "outgoing_edges" not in result
        assert "incoming_edges" not in result
        assert "edge_summary" not in result
        graph_repo.list_edges.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_with_chunks(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        embedding_callback: AsyncMock,
        search_settings: SearchSettings,
    ) -> None:
        """Includes related chunks when include_chunks is True and indexing is available."""
        node = make_node("n1", "Alice")
        graph_repo.get_node.return_value = node
        graph_repo.list_edges.return_value = []

        # Mock search_chunks to return a chunk
        chunk = make_chunk("c1", "Alice content.", source_id="src1")
        search_repo.hybrid_search = AsyncMock(return_value=[("chunk:c1", 0.9)])
        indexing_repo.get_chunk_by_id.return_value = chunk

        with (
            patch(f"{_CHUNK_HYDRATION_MODULE}.format_chunk_content") as mock_format,
            patch(f"{_CHUNK_HYDRATION_MODULE}.clean_chunk_metadata") as mock_clean,
        ):
            mock_format.return_value = ("[CHUNK C0 | ]\n[S1] Alice content.", 1)
            mock_clean.return_value = None

            handler = _make_handler(
                graph_repo,
                search_repo,
                indexing_repo=indexing_repo,
                embedding_callback=embedding_callback,
                search_settings=search_settings,
            )
            result = await handler.get_node_context(
                node_id="n1", include_chunks=True, include_edges=False
            )

        assert result["success"] is True
        assert "related_chunks" in result
        assert result["chunks_count"] >= 1

    @pytest.mark.asyncio
    async def test_context_not_found(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when the node is not found."""
        graph_repo.get_node.return_value = None

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node_context(node_id="missing")

        assert result["success"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_context_scope_check(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error when node is out of source scope."""
        node = make_node("n1", "Alice", source_id="other_src")
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node_context(node_id="n1", source_ids=["allowed_src"])

        assert result["success"] is False
        assert "not accessible" in result["error"]

    @pytest.mark.asyncio
    async def test_context_edge_source_scope_filters_related_nodes(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Related nodes from edges are filtered by source scope."""
        main_node = make_node("n1", "Alice", source_id="src_allowed")
        allowed_related = make_node("n2", "Bob", source_id="src_allowed")
        blocked_related = make_node("n3", "Eve", source_id="src_blocked")

        graph_repo.get_node.return_value = main_node

        out_edge_1 = make_edge("e1", "n1", "n2", label="knows")
        out_edge_2 = make_edge("e2", "n1", "n3", label="knows")

        graph_repo.list_edges.side_effect = lambda **kwargs: (
            [out_edge_1, out_edge_2] if kwargs.get("source_node_id") == "n1" else []
        )
        graph_repo.get_nodes_batch.return_value = [allowed_related, blocked_related]

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node_context(node_id="n1", source_ids=["src_allowed"])

        assert result["success"] is True
        # Edge to n3 is still listed (edges aren't filtered), but
        # n3 is excluded from the nodes_dict so its label becomes [unknown]
        assert len(result["outgoing_edges"]) == 2
        labels = [e["target"]["label"] for e in result["outgoing_edges"]]
        assert "Bob" in labels
        assert "[unknown]" in labels

    @pytest.mark.asyncio
    async def test_context_datetime_converted(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Datetime fields on the context node are ISO-formatted."""
        node = make_node(
            "n1",
            "Alice",
            created_at=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
            updated_at=datetime(2024, 7, 1, 15, 30, tzinfo=UTC),
        )
        graph_repo.get_node.return_value = node
        graph_repo.list_edges.return_value = []

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node_context(node_id="n1", include_edges=True)

        assert result["node"]["created_at"] == "2024-06-01T12:00:00+00:00"
        assert result["node"]["updated_at"] == "2024-07-01T15:30:00+00:00"

    @pytest.mark.asyncio
    async def test_context_no_chunks_without_indexing(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Chunks not included when include_chunks=True but no indexing repo."""
        node = make_node("n1", "Alice")
        graph_repo.get_node.return_value = node
        graph_repo.list_edges.return_value = []

        handler = _make_handler(graph_repo, search_repo, indexing_repo=None)
        result = await handler.get_node_context(
            node_id="n1", include_chunks=True, include_edges=False
        )

        assert result["success"] is True
        # No related_chunks key since indexing is None
        assert "related_chunks" not in result


# ===========================================================================
# resolve_node tests
# ===========================================================================


class TestResolveNode:
    """Tests for the resolve_node method."""

    @pytest.mark.asyncio
    async def test_keyword_resolves_directly(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Keyword search resolves when score >= 1.0 (no hybrid fallback)."""
        node = make_node("n1", "Pierre Bezukhov", "person")
        search_repo.keyword_search.return_value = [("n1", 5.0)]
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("Pierre")

        assert result["success"] is True
        assert result["resolved_node"]["id"] == "n1"
        assert result["resolved_node"]["label"] == "Pierre Bezukhov"
        assert result["confidence"] == 5.0
        assert result["search_method"] == "keyword"
        # hybrid_search should NOT be called because keyword score >= 1.0
        search_repo.hybrid_search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hybrid_fallback_on_weak_keyword(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Falls back to hybrid search when keyword results are weak (score < 1.0)."""
        node = make_node("n1", "Napoleon's Enemy", "person")
        # Keyword returns weak result
        search_repo.keyword_search.return_value = [("n1", 0.5)]
        # Hybrid returns better result
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.95)])
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("Napoleon's enemy")

        assert result["success"] is True
        assert result["search_method"] == "hybrid"
        assert result["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_hybrid_fallback_on_empty_keyword(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Falls back to hybrid when keyword search returns nothing."""
        node = make_node("n1", "The Little Princess", "character")
        search_repo.keyword_search.return_value = []
        search_repo.hybrid_search = AsyncMock(return_value=[("n1", 0.85)])
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("The Little Princess")

        assert result["success"] is True
        assert result["search_method"] == "hybrid"

    @pytest.mark.asyncio
    async def test_not_found(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Returns error when neither keyword nor hybrid finds any node."""
        search_repo.keyword_search.return_value = []
        search_repo.hybrid_search = AsyncMock(return_value=[])

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("nonexistent entity")

        assert result["success"] is False
        assert "Could not resolve" in result["error"]
        assert result["query"] == "nonexistent entity"

    @pytest.mark.asyncio
    async def test_with_alternatives(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Returns alternative matches when include_alternatives is True."""
        best = make_node("n1", "Pierre Bezukhov", "person")
        alt1 = make_node("n2", "Pierre Rostov", "person")
        alt2 = make_node("n3", "Pierre (servant)", "person")

        search_repo.keyword_search.return_value = [("n1", 5.0), ("n2", 3.0), ("n3", 2.0)]
        graph_repo.get_node.return_value = best
        graph_repo.get_nodes_batch.return_value = [alt1, alt2]

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("Pierre", include_alternatives=True, max_alternatives=3)

        assert result["success"] is True
        assert result["resolved_node"]["id"] == "n1"
        assert len(result["alternatives"]) == 2
        assert result["alternatives"][0]["id"] == "n2"
        assert result["alternatives"][0]["label"] == "Pierre Rostov"
        assert result["alternatives"][1]["id"] == "n3"

    @pytest.mark.asyncio
    async def test_without_alternatives(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Does not include alternatives when include_alternatives is False."""
        node = make_node("n1", "Alice", "person")
        search_repo.keyword_search.return_value = [("n1", 5.0), ("n2", 3.0)]
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("Alice", include_alternatives=False)

        assert result["success"] is True
        assert "alternatives" not in result

    @pytest.mark.asyncio
    async def test_source_scope_filter(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Source scope filters out nodes from disallowed sources."""
        allowed_node = make_node("n1", "Alice", source_id="src_allowed")
        blocked_node = make_node("n2", "Bob", source_id="src_blocked")

        search_repo.keyword_search.return_value = [("n1", 5.0), ("n2", 3.0)]

        def get_node_side_effect(nid: str) -> SimpleNamespace | None:
            if nid == "n1":
                return allowed_node
            if nid == "n2":
                return blocked_node
            return None

        graph_repo.get_node.side_effect = get_node_side_effect

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("test", source_ids=["src_allowed"])

        assert result["success"] is True
        assert result["resolved_node"]["id"] == "n1"

    @pytest.mark.asyncio
    async def test_source_scope_filters_all_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Returns error when all results are out of source scope."""
        blocked_node = make_node("n1", "Bob", source_id="src_blocked")
        search_repo.keyword_search.return_value = [("n1", 5.0)]
        search_repo.hybrid_search = AsyncMock(return_value=[])
        graph_repo.get_node.return_value = blocked_node

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("Bob", source_ids=["src_allowed"])

        assert result["success"] is False
        assert "Could not resolve" in result["error"]

    @pytest.mark.asyncio
    async def test_aliases_extracted_from_properties(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Extracts aliases from known alias property keys."""
        node = make_node(
            "n1",
            "Pierre Bezukhov",
            "person",
            properties={
                "aliases": ["Count Bezukhov", "Pyotr"],
                "nicknames": "Pierre",
                "age": 30,
            },
        )
        search_repo.keyword_search.return_value = [("n1", 5.0)]
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("Pierre")

        assert result["success"] is True
        assert "Count Bezukhov" in result["aliases"]
        assert "Pyotr" in result["aliases"]
        assert "Pierre" in result["aliases"]
        assert len(result["aliases"]) == 3

    @pytest.mark.asyncio
    async def test_no_aliases_when_properties_empty(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Returns empty aliases list when properties have no alias keys."""
        node = make_node("n1", "Alice", "person", properties={"age": 30})
        search_repo.keyword_search.return_value = [("n1", 5.0)]
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("Alice")

        assert result["aliases"] == []

    @pytest.mark.asyncio
    async def test_chunk_results_excluded_from_resolution(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Chunk-prefixed IDs are filtered out from resolution candidates."""
        node = make_node("n1", "Alice", "person")
        search_repo.keyword_search.return_value = [("chunk:abc", 10.0), ("n1", 5.0)]
        graph_repo.get_node.return_value = node

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("Alice")

        assert result["success"] is True
        assert result["resolved_node"]["id"] == "n1"

    @pytest.mark.asyncio
    async def test_best_node_not_found_returns_error(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Returns error when the best matching node cannot be fetched."""
        search_repo.keyword_search.return_value = [("n1", 5.0)]
        graph_repo.get_node.return_value = None

        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)
        result = await handler.resolve_node("ghost")

        assert result["success"] is False
        assert "not found" in result["error"]


# ===========================================================================
# _check_source_scope tests
# ===========================================================================


class TestCheckSourceScope:
    """Tests for the _check_source_scope helper."""

    def test_no_source_ids_allows_all(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns None (allowed) when source_ids is None."""
        node = make_node("n1", "Alice", source_id="any_src")
        handler = _make_handler(graph_repo, search_repo)

        assert handler._check_source_scope(node, None) is None

    def test_empty_source_ids_allows_all(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns None (allowed) when source_ids is empty list."""
        node = make_node("n1", "Alice", source_id="any_src")
        handler = _make_handler(graph_repo, search_repo)

        assert handler._check_source_scope(node, []) is None

    def test_node_in_scope(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns None when node's source_id is in allowed list."""
        node = make_node("n1", "Alice", source_id="src1")
        handler = _make_handler(graph_repo, search_repo)

        assert handler._check_source_scope(node, ["src1", "src2"]) is None

    def test_node_out_of_scope(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns error message when node's source_id is not in allowed list."""
        node = make_node("n1", "Alice", source_id="blocked_src")
        handler = _make_handler(graph_repo, search_repo)

        error = handler._check_source_scope(node, ["allowed_src"])
        assert error is not None
        assert "not accessible" in error
        assert "Alice" in error

    def test_node_without_source_id_allowed(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns None when node has no source_id (not scoped to any source)."""
        node = make_node("n1", "Alice", source_id=None)
        handler = _make_handler(graph_repo, search_repo)

        assert handler._check_source_scope(node, ["src1"]) is None


# ===========================================================================
# _make_embedding_callback tests
# ===========================================================================


class TestMakeEmbeddingCallback:
    """Tests for the _make_embedding_callback helper."""

    def test_returns_callback_when_set(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Returns the injected callback when available."""
        handler = _make_handler(graph_repo, search_repo, embedding_callback=embedding_callback)

        assert handler._make_embedding_callback() is embedding_callback

    def test_returns_none_when_not_set(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Returns None when no embedding callback was provided."""
        handler = _make_handler(graph_repo, search_repo, embedding_callback=None)

        assert handler._make_embedding_callback() is None


# ===========================================================================
# tool_handler decorator error handling tests
# ===========================================================================


class TestToolHandlerDecorator:
    """Tests verifying that @tool_handler catches exceptions for decorated methods."""

    @pytest.mark.asyncio
    async def test_get_node_exception_caught(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Exception in get_node is caught by @tool_handler decorator."""
        graph_repo.get_node.side_effect = RuntimeError("DB crash")

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node(node_id="n1")

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_update_node_exception_caught(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Exception in update_node is caught by @tool_handler decorator."""
        graph_repo.update_node.side_effect = RuntimeError("DB crash")

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.update_node(node_id="n1", label="Boom")

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_delete_node_exception_caught(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Exception in delete_node is caught by @tool_handler decorator."""
        graph_repo.delete_node.side_effect = RuntimeError("DB crash")

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.delete_node(node_id="n1")

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_get_node_context_exception_caught(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Exception in get_node_context is caught by @tool_handler decorator."""
        graph_repo.get_node.side_effect = RuntimeError("DB crash")

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.get_node_context(node_id="n1")

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_resolve_node_exception_caught(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Exception in resolve_node is caught by @tool_handler decorator."""
        search_repo.keyword_search.side_effect = RuntimeError("Search crash")

        handler = _make_handler(graph_repo, search_repo)
        result = await handler.resolve_node("test")

        assert result["success"] is False
        assert result["error"] == "Operation failed"

    @pytest.mark.asyncio
    async def test_search_chunks_exception_caught(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        indexing_repo: MagicMock,
        search_settings: SearchSettings,
    ) -> None:
        """Exception in search_chunks is caught by @tool_handler decorator."""
        search_repo.hybrid_search = AsyncMock(side_effect=RuntimeError("Search crash"))

        handler = _make_handler(
            graph_repo,
            search_repo,
            indexing_repo=indexing_repo,
            search_settings=search_settings,
        )
        result = await handler.search_chunks("test")

        assert result["success"] is False
        assert result["error"] == "Operation failed"

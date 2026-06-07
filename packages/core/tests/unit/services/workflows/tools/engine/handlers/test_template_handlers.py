# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for TemplateToolHandlers.

Covers template listing, creation, deletion, semantic search, keyword
fallback, EmbedResult handling (the historical bug), synonym expansion,
usage-count sorting, and the @tool_handler error wrapping.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.models import PropertyType
from chaoscypher_core.services.workflows.tools.engine.handlers.template_handlers import (
    TemplateToolHandlers,
)


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def make_property(
    name: str = "age",
    display_name: str = "Age",
    property_type: PropertyType = PropertyType.INTEGER,
    required: bool = False,
) -> SimpleNamespace:
    """Create a minimal property object mimicking a PropertyDefinition.

    Args:
        name: Property name.
        display_name: Human-readable label.
        property_type: Enum member whose ``.value`` is serialised.
        required: Whether the property is required.

    Returns:
        A ``SimpleNamespace`` with the fields consumed by
        ``TemplateToolHandlers.list_templates``.

    """
    return SimpleNamespace(
        name=name,
        display_name=display_name,
        property_type=property_type,
        required=required,
    )


def make_template(
    tid: str = "t1",
    name: str = "Person",
    template_type: str = "node",
    description: str = "A person template",
    properties: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    """Create a minimal template object for mocking graph repository returns.

    Args:
        tid: Template identifier.
        name: Human-readable template name.
        template_type: ``"node"`` or ``"edge"``.
        description: Template description.
        properties: Optional list of property mocks.

    Returns:
        A ``SimpleNamespace`` mimicking a template entity.

    """
    return SimpleNamespace(
        id=tid,
        name=name,
        template_type=template_type,
        description=description,
        properties=properties or [],
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def graph_repo() -> MagicMock:
    """Mock graph repository with template-related methods."""
    repo = MagicMock()
    repo.list_templates.return_value = []
    repo.create_template.return_value = make_template()
    repo.delete_template.return_value = True
    repo.get_template.return_value = None
    repo.get_template_usage_counts.return_value = {}
    return repo


@pytest.fixture
def search_repo() -> MagicMock:
    """Mock search repository with template_semantic_search."""
    repo = MagicMock()
    repo.template_semantic_search.return_value = []
    return repo


@pytest.fixture
def embedding_callback() -> AsyncMock:
    """Async embedding callback returning a simple EmbedResult-like object."""
    cb = AsyncMock()
    cb.return_value = SimpleNamespace(embedding=[0.1, 0.2, 0.3])
    return cb


def _make_handler(
    graph_repo: MagicMock,
    search_repo: MagicMock | None = None,
    embedding_callback: AsyncMock | None = None,
) -> TemplateToolHandlers:
    """Construct a ``TemplateToolHandlers`` instance from mocks.

    Args:
        graph_repo: Mock graph repository.
        search_repo: Optional mock search repository.
        embedding_callback: Optional async embedding callback.

    Returns:
        Configured handler ready for testing.

    """
    return TemplateToolHandlers(
        graph_repository=graph_repo,
        search_repository=search_repo,
        embedding_callback=embedding_callback,
    )


# ===========================================================================
# list_templates Tests
# ===========================================================================


class TestListTemplates:
    """Tests for TemplateToolHandlers.list_templates."""

    @pytest.mark.asyncio
    async def test_list_all_templates(self, graph_repo: MagicMock) -> None:
        """Listing without type filter returns all templates from graph repo."""
        templates = [
            make_template("t1", "Person", "node"),
            make_template("t2", "Knows", "edge"),
        ]
        graph_repo.list_templates.return_value = templates

        handler = _make_handler(graph_repo)
        result = await handler.list_templates()

        assert result["success"] is True
        assert result["count"] == 2
        assert len(result["templates"]) == 2
        assert result["templates"][0]["id"] == "t1"
        assert result["templates"][0]["name"] == "Person"
        assert result["templates"][1]["template_type"] == "edge"
        graph_repo.list_templates.assert_called_once_with(template_type=None)

    @pytest.mark.asyncio
    async def test_list_templates_filter_by_type(self, graph_repo: MagicMock) -> None:
        """Passing template_type forwards the filter to the graph repo."""
        graph_repo.list_templates.return_value = [
            make_template("t1", "Person", "node"),
        ]

        handler = _make_handler(graph_repo)
        result = await handler.list_templates(template_type="node")

        assert result["success"] is True
        assert result["count"] == 1
        graph_repo.list_templates.assert_called_once_with(template_type="node")

    @pytest.mark.asyncio
    async def test_list_templates_empty(self, graph_repo: MagicMock) -> None:
        """Empty template list returns count 0 and empty templates array."""
        graph_repo.list_templates.return_value = []

        handler = _make_handler(graph_repo)
        result = await handler.list_templates()

        assert result["success"] is True
        assert result["count"] == 0
        assert result["templates"] == []

    @pytest.mark.asyncio
    async def test_list_templates_serializes_properties(
        self,
        graph_repo: MagicMock,
    ) -> None:
        """Template properties are serialised with property_type.value."""
        props = [
            make_property("name", "Name", PropertyType.STRING, required=True),
            make_property("age", "Age", PropertyType.INTEGER, required=False),
        ]
        template = make_template("t1", "Person", "node", properties=props)
        graph_repo.list_templates.return_value = [template]

        handler = _make_handler(graph_repo)
        result = await handler.list_templates()

        serialised = result["templates"][0]["properties"]
        assert len(serialised) == 2
        assert serialised[0] == {
            "name": "name",
            "display_name": "Name",
            "property_type": "string",
            "required": True,
        }
        assert serialised[1] == {
            "name": "age",
            "display_name": "Age",
            "property_type": "integer",
            "required": False,
        }

    @pytest.mark.asyncio
    async def test_list_templates_none_properties(self, graph_repo: MagicMock) -> None:
        """Templates with properties=None serialize to an empty list."""
        template = make_template("t1", "Person", "node")
        template.properties = None
        graph_repo.list_templates.return_value = [template]

        handler = _make_handler(graph_repo)
        result = await handler.list_templates()

        assert result["templates"][0]["properties"] == []

    @pytest.mark.asyncio
    async def test_list_templates_exception_wrapped(
        self,
        graph_repo: MagicMock,
    ) -> None:
        """@tool_handler wraps unexpected exceptions into a failure dict."""
        graph_repo.list_templates.side_effect = RuntimeError("db unavailable")

        handler = _make_handler(graph_repo)
        result = await handler.list_templates()

        assert result["success"] is False
        assert result["error"] == "Operation failed"


# ===========================================================================
# create_template Tests
# ===========================================================================


class TestCreateTemplate:
    """Tests for TemplateToolHandlers.create_template."""

    @pytest.mark.asyncio
    async def test_create_template_basic(self, graph_repo: MagicMock) -> None:
        """Basic creation without properties returns success and template_id."""
        created = make_template("new-t1", "Event", "node")
        graph_repo.create_template.return_value = created

        handler = _make_handler(graph_repo)
        result = await handler.create_template(
            name="Event",
            template_type="node",
            description="An event",
        )

        assert result["success"] is True
        assert result["template_id"] == "new-t1"
        assert "Created template: Event" in result["message"]

        # Verify create_template was called with a TemplateCreate model
        call_args = graph_repo.create_template.call_args
        tc = call_args[0][0]
        assert tc.name == "Event"
        assert tc.template_type == "node"
        assert tc.description == "An event"
        assert tc.properties == []

    @pytest.mark.asyncio
    async def test_create_template_with_properties(
        self,
        graph_repo: MagicMock,
    ) -> None:
        """Property dicts are converted to PropertyDefinition objects."""
        created = make_template("new-t2", "Person", "node")
        graph_repo.create_template.return_value = created

        handler = _make_handler(graph_repo)
        result = await handler.create_template(
            name="Person",
            template_type="node",
            description="A person",
            properties=[
                {
                    "name": "full_name",
                    "display_name": "Full Name",
                    "property_type": "string",
                    "required": True,
                },
                {
                    "name": "birth_year",
                    "display_name": "Birth Year",
                    "property_type": "integer",
                    "required": False,
                },
            ],
        )

        assert result["success"] is True

        call_args = graph_repo.create_template.call_args
        tc = call_args[0][0]
        assert len(tc.properties) == 2
        assert tc.properties[0].name == "full_name"
        assert tc.properties[0].property_type == PropertyType.STRING
        assert tc.properties[0].required is True
        assert tc.properties[1].name == "birth_year"
        assert tc.properties[1].property_type == PropertyType.INTEGER

    @pytest.mark.asyncio
    async def test_create_template_none_properties_defaults_to_empty(
        self,
        graph_repo: MagicMock,
    ) -> None:
        """Passing properties=None defaults to an empty list."""
        created = make_template("t-new", "Tag", "node")
        graph_repo.create_template.return_value = created

        handler = _make_handler(graph_repo)
        result = await handler.create_template(
            name="Tag",
            template_type="node",
        )

        assert result["success"] is True
        call_args = graph_repo.create_template.call_args
        tc = call_args[0][0]
        assert tc.properties == []

    @pytest.mark.asyncio
    async def test_create_template_exception_wrapped(
        self,
        graph_repo: MagicMock,
    ) -> None:
        """@tool_handler wraps creation errors into a failure dict."""
        graph_repo.create_template.side_effect = ValueError("bad template")

        handler = _make_handler(graph_repo)
        result = await handler.create_template(
            name="Bad",
            template_type="node",
        )

        assert result["success"] is False
        assert result["error"] == "Operation failed"


# ===========================================================================
# delete_template Tests
# ===========================================================================


class TestDeleteTemplate:
    """Tests for TemplateToolHandlers.delete_template."""

    @pytest.mark.asyncio
    async def test_delete_template_success(self, graph_repo: MagicMock) -> None:
        """Successful deletion returns success=True with a message."""
        graph_repo.delete_template.return_value = True

        handler = _make_handler(graph_repo)
        result = await handler.delete_template("t1")

        assert result["success"] is True
        assert "t1" in result["message"]
        graph_repo.delete_template.assert_called_once_with("t1")

    @pytest.mark.asyncio
    async def test_delete_template_failure(self, graph_repo: MagicMock) -> None:
        """Failed deletion (returns False) yields success=False with error."""
        graph_repo.delete_template.return_value = False

        handler = _make_handler(graph_repo)
        result = await handler.delete_template("nonexistent")

        assert result["success"] is False
        assert result["error"] == "Failed to delete template"

    @pytest.mark.asyncio
    async def test_delete_template_exception_wrapped(
        self,
        graph_repo: MagicMock,
    ) -> None:
        """@tool_handler wraps delete exceptions into a failure dict."""
        graph_repo.delete_template.side_effect = RuntimeError("db locked")

        handler = _make_handler(graph_repo)
        result = await handler.delete_template("t1")

        assert result["success"] is False
        assert result["error"] == "Operation failed"


# ===========================================================================
# search_templates Tests — Semantic Search
# ===========================================================================


class TestSearchTemplatesSemantic:
    """Tests for the semantic (embedding-based) path of search_templates."""

    @pytest.mark.asyncio
    async def test_semantic_search_happy_path(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Full semantic search returns ranked templates with scores.

        Verifies that:
        - Embedding callback is invoked with the query
        - template_semantic_search is called with the embedding
        - Template details are fetched and included in results
        """
        person = make_template("t1", "Person", "node", "A person template")
        graph_repo.get_template.return_value = person
        graph_repo.list_templates.return_value = []  # no keyword matches
        search_repo.template_semantic_search.return_value = [("t1", 0.92)]

        handler = _make_handler(graph_repo, search_repo, embedding_callback)
        result = await handler.search_templates("people")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["templates"][0]["id"] == "t1"
        assert result["templates"][0]["similarity_score"] == 0.92
        embedding_callback.assert_awaited_once_with("people")
        search_repo.template_semantic_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_result_object_handling(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """EmbedResult object with .embedding attribute is handled correctly.

        This is the historical bug — the callback returned an object with
        an ``embedding`` attribute instead of a dict. The handler must use
        ``hasattr(result, "embedding")`` to extract the vector.
        """
        embed_result = SimpleNamespace(embedding=[0.5, 0.6, 0.7])
        callback = AsyncMock(return_value=embed_result)

        person = make_template("t1", "Person", "node")
        graph_repo.get_template.return_value = person
        graph_repo.list_templates.return_value = []
        search_repo.template_semantic_search.return_value = [("t1", 0.88)]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        # Verify the embedding was extracted correctly
        call_args = search_repo.template_semantic_search.call_args
        assert call_args.kwargs.get("query_embedding") == [0.5, 0.6, 0.7]

    @pytest.mark.asyncio
    async def test_dict_embedding_handling(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Dict-style embedding result {'embedding': [...]} is extracted correctly."""
        callback = AsyncMock(return_value={"embedding": [0.1, 0.2, 0.3]})

        person = make_template("t1", "Person", "node")
        graph_repo.get_template.return_value = person
        graph_repo.list_templates.return_value = []
        search_repo.template_semantic_search.return_value = [("t1", 0.75)]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        call_args = search_repo.template_semantic_search.call_args
        assert call_args.kwargs.get("query_embedding") == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_raw_list_embedding_handling(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Raw list returned directly by the callback is used as-is.

        When the callback returns neither a dict nor an object with
        ``.embedding``, the result itself is used as the query embedding.
        """
        callback = AsyncMock(return_value=[0.4, 0.5, 0.6])

        person = make_template("t1", "Person", "node")
        graph_repo.get_template.return_value = person
        graph_repo.list_templates.return_value = []
        search_repo.template_semantic_search.return_value = [("t1", 0.81)]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        call_args = search_repo.template_semantic_search.call_args
        assert call_args.kwargs.get("query_embedding") == [0.4, 0.5, 0.6]

    @pytest.mark.asyncio
    async def test_empty_embedding_falls_back_to_keyword(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Empty embedding vector triggers keyword fallback.

        When the embedding callback returns an empty list (e.g. model error),
        the handler should fall back to keyword search rather than calling
        template_semantic_search with an empty vector.
        """
        callback = AsyncMock(return_value={"embedding": []})

        person = make_template("t1", "Person", "node", description="people template")
        graph_repo.list_templates.return_value = [person]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        assert result.get("search_method") == "keyword_fallback"
        search_repo.template_semantic_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_embed_result_object_falls_back(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """EmbedResult with empty embedding attribute triggers keyword fallback."""
        callback = AsyncMock(return_value=SimpleNamespace(embedding=[]))

        person = make_template("t1", "Person", "node", description="people template")
        graph_repo.list_templates.return_value = [person]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        assert result.get("search_method") == "keyword_fallback"
        search_repo.template_semantic_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_search_repo_falls_back_to_keyword(
        self,
        graph_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Missing search repository forces keyword fallback immediately."""
        person = make_template("t1", "Person", "node", description="people")
        graph_repo.list_templates.return_value = [person]

        handler = _make_handler(graph_repo, search_repo=None, embedding_callback=embedding_callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        assert result.get("search_method") == "keyword_fallback"
        embedding_callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_embedding_callback_falls_back_to_keyword(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Missing embedding callback forces keyword fallback immediately."""
        person = make_template("t1", "Person", "node", description="people")
        graph_repo.list_templates.return_value = [person]

        handler = _make_handler(graph_repo, search_repo=search_repo, embedding_callback=None)
        result = await handler.search_templates("person")

        assert result["success"] is True
        assert result.get("search_method") == "keyword_fallback"

    @pytest.mark.asyncio
    async def test_template_type_filter_in_semantic_search(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Semantic search filters results by template_type when specified.

        Templates whose type does not match the filter are excluded even
        if they have high similarity scores.
        """
        node_template = make_template("t1", "Person", "node")
        edge_template = make_template("t2", "Knows", "edge")

        def get_template_side_effect(tid: str) -> SimpleNamespace | None:
            if tid == "t1":
                return node_template
            if tid == "t2":
                return edge_template
            return None

        graph_repo.get_template.side_effect = get_template_side_effect
        graph_repo.list_templates.return_value = []  # no keyword matches
        search_repo.template_semantic_search.return_value = [
            ("t1", 0.95),
            ("t2", 0.90),
        ]

        handler = _make_handler(graph_repo, search_repo, embedding_callback)
        result = await handler.search_templates("person", template_type="node")

        assert result["success"] is True
        template_types = [t["template_type"] for t in result["templates"]]
        assert "edge" not in template_types
        assert "node" in template_types

    @pytest.mark.asyncio
    async def test_keyword_merge_deduplicates(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Templates found by both semantic and keyword search appear only once.

        The handler tracks ``seen_ids`` so that keyword results already
        present from semantic search are not duplicated.
        """
        person = make_template("t1", "Person", "node", description="A person")

        graph_repo.get_template.return_value = person
        # Keyword search also finds the same template
        graph_repo.list_templates.return_value = [person]
        search_repo.template_semantic_search.return_value = [("t1", 0.90)]

        handler = _make_handler(graph_repo, search_repo, embedding_callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        ids = [t["id"] for t in result["templates"]]
        assert ids.count("t1") == 1

    @pytest.mark.asyncio
    async def test_keyword_merge_adds_new_matches(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Keyword-only matches are appended when not in semantic results."""
        semantic_template = make_template("t1", "Person", "node", "A person")
        keyword_template = make_template("t2", "Character", "node", "A character")

        def get_template_side_effect(tid: str) -> SimpleNamespace | None:
            if tid == "t1":
                return semantic_template
            return None

        graph_repo.get_template.side_effect = get_template_side_effect
        graph_repo.list_templates.return_value = [keyword_template]
        search_repo.template_semantic_search.return_value = [("t1", 0.90)]

        handler = _make_handler(graph_repo, search_repo, embedding_callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        ids = [t["id"] for t in result["templates"]]
        assert "t1" in ids
        assert "t2" in ids

    @pytest.mark.asyncio
    async def test_usage_count_sorting(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Templates are sorted by entity_count desc, then similarity_score desc.

        A template with more entities should rank above one with a higher
        similarity score but fewer entities.
        """
        t1 = make_template("t1", "Unused", "node")
        t2 = make_template("t2", "Popular", "node")

        def get_template_side_effect(tid: str) -> SimpleNamespace | None:
            if tid == "t1":
                return t1
            if tid == "t2":
                return t2
            return None

        graph_repo.get_template.side_effect = get_template_side_effect
        graph_repo.list_templates.return_value = []
        graph_repo.get_template_usage_counts.return_value = {
            "t1": {"nodes": 0, "edges": 0},
            "t2": {"nodes": 50, "edges": 10},
        }
        search_repo.template_semantic_search.return_value = [
            ("t1", 0.99),  # Higher similarity but no usage
            ("t2", 0.70),  # Lower similarity but much more usage
        ]

        handler = _make_handler(graph_repo, search_repo, embedding_callback)
        result = await handler.search_templates("anything")

        assert result["success"] is True
        assert len(result["templates"]) == 2
        # Popular template should be first despite lower similarity
        assert result["templates"][0]["id"] == "t2"
        assert result["templates"][0]["entity_count"] == 60
        assert result["templates"][1]["id"] == "t1"
        assert result["templates"][1]["entity_count"] == 0

    @pytest.mark.asyncio
    async def test_usage_counts_without_hasattr(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """When graph repo lacks get_template_usage_counts, entity_count defaults to 0.

        The handler checks ``hasattr(self.graph, 'get_template_usage_counts')``
        before calling it. With a ``spec``-constrained mock that lacks the
        method, counts should default to zero.
        """
        person = make_template("t1", "Person", "node")
        graph_repo.get_template.return_value = person
        graph_repo.list_templates.return_value = []
        search_repo.template_semantic_search.return_value = [("t1", 0.85)]

        # Remove the method so hasattr returns False
        del graph_repo.get_template_usage_counts

        handler = _make_handler(graph_repo, search_repo, embedding_callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        assert result["templates"][0]["entity_count"] == 0

    @pytest.mark.asyncio
    async def test_semantic_search_respects_limit(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Final result list is truncated to the requested limit."""
        templates = [make_template(f"t{i}", f"Template{i}", "node") for i in range(10)]

        def get_template_side_effect(tid: str) -> SimpleNamespace | None:
            for t in templates:
                if t.id == tid:
                    return t
            return None

        graph_repo.get_template.side_effect = get_template_side_effect
        graph_repo.list_templates.return_value = []
        search_repo.template_semantic_search.return_value = [
            (f"t{i}", 0.9 - i * 0.05) for i in range(10)
        ]

        handler = _make_handler(graph_repo, search_repo, embedding_callback)
        result = await handler.search_templates("test", limit=3)

        assert result["success"] is True
        assert result["count"] <= 3

    @pytest.mark.asyncio
    async def test_semantic_search_skips_missing_templates(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """Templates not found by get_template are silently skipped."""
        graph_repo.get_template.return_value = None  # template not found
        graph_repo.list_templates.return_value = []
        search_repo.template_semantic_search.return_value = [
            ("missing-t1", 0.95),
        ]

        handler = _make_handler(graph_repo, search_repo, embedding_callback)
        result = await handler.search_templates("phantom")

        assert result["success"] is True
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_search_templates_exception_wrapped(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
        embedding_callback: AsyncMock,
    ) -> None:
        """@tool_handler wraps search exceptions into a failure dict."""
        embedding_callback.side_effect = RuntimeError("model crashed")
        # Also make keyword fallback fail to ensure the decorator catches it
        graph_repo.list_templates.side_effect = RuntimeError("db down too")

        handler = _make_handler(graph_repo, search_repo, embedding_callback)
        result = await handler.search_templates("anything")

        assert result["success"] is False
        assert result["error"] == "Operation failed"


# ===========================================================================
# _keyword_search_templates Tests
# ===========================================================================


class TestKeywordSearchTemplates:
    """Tests for the keyword fallback path (_keyword_search_templates)."""

    @pytest.mark.asyncio
    async def test_exact_name_match(self, graph_repo: MagicMock) -> None:
        """Exact query term in template name scores 1.0."""
        person = make_template("t1", "Person", "node", "A person template")
        graph_repo.list_templates.return_value = [person]

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("person")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["templates"][0]["similarity_score"] == 1.0
        assert result["search_method"] == "keyword_fallback"

    @pytest.mark.asyncio
    async def test_synonym_name_match(self, graph_repo: MagicMock) -> None:
        """Synonym of query term in template name scores 0.85.

        Searching for 'people' should find 'Person' because 'person' is
        a synonym of 'people' in TEMPLATE_SYNONYMS.
        """
        person = make_template("t1", "Person", "node", "Template for persons")
        graph_repo.list_templates.return_value = [person]

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("people")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["templates"][0]["similarity_score"] == 0.85

    @pytest.mark.asyncio
    async def test_exact_description_match(self, graph_repo: MagicMock) -> None:
        """Exact query term in description (not name) scores 0.7."""
        template = make_template("t1", "Entity", "node", "Represents a location")
        graph_repo.list_templates.return_value = [template]

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("location")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["templates"][0]["similarity_score"] == 0.7

    @pytest.mark.asyncio
    async def test_synonym_description_match(self, graph_repo: MagicMock) -> None:
        """Synonym in description (not name) scores 0.6.

        Searching for 'place' should find a template whose description
        contains 'location' via synonym expansion.
        """
        template = make_template("t1", "Geo", "node", "A location in the world")
        graph_repo.list_templates.return_value = [template]

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("place")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["templates"][0]["similarity_score"] == 0.6

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, graph_repo: MagicMock) -> None:
        """Completely unrelated query produces zero results."""
        template = make_template("t1", "Person", "node", "A person")
        graph_repo.list_templates.return_value = [template]

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("xyzzy_nonexistent")

        assert result["success"] is True
        assert result["count"] == 0
        assert result["templates"] == []

    @pytest.mark.asyncio
    async def test_type_filter(self, graph_repo: MagicMock) -> None:
        """template_type is forwarded to graph.list_templates for filtering."""
        graph_repo.list_templates.return_value = [
            make_template("t1", "Person", "node"),
        ]

        handler = _make_handler(graph_repo)
        await handler._keyword_search_templates("person", template_type="node")

        graph_repo.list_templates.assert_called_with(template_type="node")

    @pytest.mark.asyncio
    async def test_best_score_wins(self, graph_repo: MagicMock) -> None:
        """When multiple terms match, the highest score is used.

        If the exact term matches the name (score=1.0) and a synonym
        matches the description (score=0.6), the result should carry 1.0.
        """
        template = make_template("t1", "Person", "node", "A human being")
        graph_repo.list_templates.return_value = [template]

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("person")

        assert result["templates"][0]["similarity_score"] == 1.0

    @pytest.mark.asyncio
    async def test_keyword_usage_count_sorting(self, graph_repo: MagicMock) -> None:
        """Keyword results are sorted by entity_count first, then by score."""
        t1 = make_template("t1", "Person", "node", "Rarely used person")
        t2 = make_template("t2", "Character", "node", "Popular character person")
        graph_repo.list_templates.return_value = [t1, t2]
        graph_repo.get_template_usage_counts.return_value = {
            "t1": {"nodes": 2, "edges": 0},
            "t2": {"nodes": 100, "edges": 20},
        }

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("person")

        assert result["success"] is True
        assert result["count"] == 2
        # t2 has more entities, so it should be first
        assert result["templates"][0]["id"] == "t2"
        assert result["templates"][0]["entity_count"] == 120
        assert result["templates"][1]["id"] == "t1"
        assert result["templates"][1]["entity_count"] == 2

    @pytest.mark.asyncio
    async def test_keyword_usage_counts_without_hasattr(
        self,
        graph_repo: MagicMock,
    ) -> None:
        """Without get_template_usage_counts, entity_count defaults to 0."""
        person = make_template("t1", "Person", "node")
        graph_repo.list_templates.return_value = [person]

        del graph_repo.get_template_usage_counts

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("person")

        assert result["success"] is True
        assert result["templates"][0]["entity_count"] == 0

    @pytest.mark.asyncio
    async def test_keyword_respects_limit(self, graph_repo: MagicMock) -> None:
        """Results are truncated to the requested limit."""
        templates = [make_template(f"t{i}", f"Person{i}", "node", "person") for i in range(10)]
        graph_repo.list_templates.return_value = templates

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("person", limit=3)

        assert result["success"] is True
        assert result["count"] <= 3

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self, graph_repo: MagicMock) -> None:
        """Keyword matching is case-insensitive for both name and description."""
        template = make_template("t1", "PERSON", "node", "An INDIVIDUAL entity")
        graph_repo.list_templates.return_value = [template]

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("person")

        assert result["success"] is True
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_none_description_handled(self, graph_repo: MagicMock) -> None:
        """Templates with None description do not cause errors."""
        template = make_template("t1", "Person", "node", description="")
        template.description = None
        graph_repo.list_templates.return_value = [template]

        handler = _make_handler(graph_repo)
        result = await handler._keyword_search_templates("person")

        assert result["success"] is True
        assert result["count"] == 1


# ===========================================================================
# EmbedResult Bug Regression Tests
# ===========================================================================


class TestEmbedResultRegression:
    """Regression tests for the EmbedResult handling bug.

    The original bug: the embedding callback returned an ``EmbedResult``
    object with an ``.embedding`` attribute, but the handler tried to use
    it as a dict (``result.get("embedding")``), causing an AttributeError.

    The fix checks ``isinstance(result, dict)`` first, then
    ``hasattr(result, "embedding")``, then falls through to using the
    result directly (for raw list returns).
    """

    @pytest.mark.asyncio
    async def test_dict_with_embedding_key(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Dict result with 'embedding' key is handled via dict access."""
        callback = AsyncMock(return_value={"embedding": [1.0, 2.0, 3.0]})
        graph_repo.get_template.return_value = make_template("t1", "Test", "node")
        graph_repo.list_templates.return_value = []
        search_repo.template_semantic_search.return_value = [("t1", 0.9)]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("test")

        assert result["success"] is True
        call_args = search_repo.template_semantic_search.call_args
        assert call_args.kwargs["query_embedding"] == [1.0, 2.0, 3.0]

    @pytest.mark.asyncio
    async def test_dict_without_embedding_key(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Dict without 'embedding' key yields empty list, triggering fallback."""
        callback = AsyncMock(return_value={"other_key": "value"})

        person = make_template("t1", "Person", "node", description="person template")
        graph_repo.list_templates.return_value = [person]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        assert result.get("search_method") == "keyword_fallback"
        search_repo.template_semantic_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_object_with_embedding_attribute(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Object with .embedding attribute is handled via attribute access."""
        embed_obj = SimpleNamespace(embedding=[4.0, 5.0, 6.0])
        callback = AsyncMock(return_value=embed_obj)

        graph_repo.get_template.return_value = make_template("t1", "Test", "node")
        graph_repo.list_templates.return_value = []
        search_repo.template_semantic_search.return_value = [("t1", 0.85)]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("test")

        assert result["success"] is True
        call_args = search_repo.template_semantic_search.call_args
        assert call_args.kwargs["query_embedding"] == [4.0, 5.0, 6.0]

    @pytest.mark.asyncio
    async def test_object_with_empty_embedding(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Object with empty .embedding triggers keyword fallback."""
        embed_obj = SimpleNamespace(embedding=[])
        callback = AsyncMock(return_value=embed_obj)

        person = make_template("t1", "Person", "node", description="people")
        graph_repo.list_templates.return_value = [person]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        assert result.get("search_method") == "keyword_fallback"

    @pytest.mark.asyncio
    async def test_raw_list_embedding(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """Raw list returned by callback is used directly as the embedding."""
        callback = AsyncMock(return_value=[7.0, 8.0, 9.0])

        graph_repo.get_template.return_value = make_template("t1", "Test", "node")
        graph_repo.list_templates.return_value = []
        search_repo.template_semantic_search.return_value = [("t1", 0.77)]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("test")

        assert result["success"] is True
        call_args = search_repo.template_semantic_search.call_args
        assert call_args.kwargs["query_embedding"] == [7.0, 8.0, 9.0]

    @pytest.mark.asyncio
    async def test_none_result_triggers_fallback(
        self,
        graph_repo: MagicMock,
        search_repo: MagicMock,
    ) -> None:
        """None result from callback triggers keyword fallback.

        ``None`` is not a dict, has no ``.embedding``, and is falsy, so
        the handler should treat it as an empty embedding and fall back.
        """
        callback = AsyncMock(return_value=None)

        person = make_template("t1", "Person", "node", description="people")
        graph_repo.list_templates.return_value = [person]

        handler = _make_handler(graph_repo, search_repo, callback)
        result = await handler.search_templates("person")

        assert result["success"] is True
        assert result.get("search_method") == "keyword_fallback"
        search_repo.template_semantic_search.assert_not_called()

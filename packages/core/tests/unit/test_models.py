# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for Engine convenience models.

Verifies DatabaseStats, ProcessingResult, and EngineSearchResult Pydantic models
behave correctly with required fields, defaults, and serialization.
"""

import json

import pytest
from pydantic import ValidationError

from chaoscypher_core.models import (
    BatchEmbedResult,
    DatabaseStats,
    EmbedResult,
    EngineSearchResult,
    HealthReport,
    HealthResult,
    IndexingResult,
    LLMChatResponse,
    ProcessingResult,
    RebuildResult,
    TokenUsage,
    ToolResult,
)


@pytest.mark.unit
@pytest.mark.core
class TestDatabaseStats:
    """Test DatabaseStats model."""

    def test_create_with_all_fields(self):
        """DatabaseStats can be created with all fields."""
        stats = DatabaseStats(
            database_name="test_db",
            data_dir="/data/databases/test_db",
            nodes=42,
            edges=17,
            templates=5,
        )
        assert stats.database_name == "test_db"
        assert stats.data_dir == "/data/databases/test_db"
        assert stats.nodes == 42
        assert stats.edges == 17
        assert stats.templates == 5

    def test_model_dump_returns_dict(self):
        """model_dump() returns a dict."""
        stats = DatabaseStats(
            database_name="db",
            data_dir="/path",
            nodes=0,
            edges=0,
            templates=0,
        )
        dumped = stats.model_dump()
        assert isinstance(dumped, dict)
        assert dumped["database_name"] == "db"
        assert dumped["nodes"] == 0

    def test_model_dump_json_returns_string(self):
        """model_dump_json() returns a JSON string."""
        stats = DatabaseStats(
            database_name="db",
            data_dir="/path",
            nodes=1,
            edges=2,
            templates=3,
        )
        json_str = stats.model_dump_json()
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["nodes"] == 1

    def test_extra_fields_forbidden(self):
        """Extra fields are rejected."""
        with pytest.raises(ValidationError):
            DatabaseStats(
                database_name="db",
                data_dir="/path",
                nodes=0,
                edges=0,
                templates=0,
                extra_field="not_allowed",
            )


@pytest.mark.unit
@pytest.mark.core
class TestProcessingResult:
    """Test ProcessingResult model."""

    def test_create_with_all_fields(self):
        """ProcessingResult can be created with all fields."""
        result = ProcessingResult(
            source_id="src-001",
            nodes=["n1", "n2"],
            edges=["e1"],
            templates=["t1", "t2", "t3"],
        )
        assert result.source_id == "src-001"
        assert result.nodes == ["n1", "n2"]
        assert result.edges == ["e1"]
        assert result.templates == ["t1", "t2", "t3"]

    def test_default_lists_are_empty(self):
        """Lists default to empty when not provided."""
        result = ProcessingResult(source_id="src-002")
        assert result.nodes == []
        assert result.edges == []
        assert result.templates == []

    def test_model_dump_returns_dict(self):
        """model_dump() returns a dict."""
        result = ProcessingResult(source_id="src-003")
        dumped = result.model_dump()
        assert isinstance(dumped, dict)
        assert dumped["source_id"] == "src-003"
        assert dumped["nodes"] == []

    def test_model_dump_json_returns_string(self):
        """model_dump_json() returns a JSON string."""
        result = ProcessingResult(
            source_id="src-004",
            nodes=["n1"],
        )
        json_str = result.model_dump_json()
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["source_id"] == "src-004"
        assert parsed["nodes"] == ["n1"]

    def test_extra_fields_forbidden(self):
        """Extra fields are rejected."""
        with pytest.raises(ValidationError):
            ProcessingResult(source_id="src-005", extra="bad")


@pytest.mark.unit
@pytest.mark.core
class TestEngineSearchResult:
    """Test EngineSearchResult model."""

    def test_create_with_all_fields(self):
        """EngineSearchResult can be created with all fields."""
        result = EngineSearchResult(
            label="Test Node",
            score=0.95,
            result_type="node",
            id="node-001",
            template_id="tmpl-001",
            source="document.pdf",
            content="Some content preview",
        )
        assert result.label == "Test Node"
        assert result.score == 0.95
        assert result.result_type == "node"
        assert result.id == "node-001"
        assert result.template_id == "tmpl-001"
        assert result.source == "document.pdf"
        assert result.content == "Some content preview"

    def test_optional_fields_default_to_none(self):
        """Optional fields default to None."""
        result = EngineSearchResult(
            label="Chunk result",
            score=0.8,
            result_type="chunk",
            id="chunk-001",
        )
        assert result.template_id is None
        assert result.source is None
        assert result.content is None

    def test_model_dump_returns_dict(self):
        """model_dump() returns a dict."""
        result = EngineSearchResult(
            label="Test",
            score=0.5,
            result_type="node",
            id="n1",
        )
        dumped = result.model_dump()
        assert isinstance(dumped, dict)
        assert dumped["label"] == "Test"
        assert dumped["template_id"] is None

    def test_model_dump_json_returns_string(self):
        """model_dump_json() returns a JSON string."""
        result = EngineSearchResult(
            label="Test",
            score=0.7,
            result_type="chunk",
            id="c1",
            content="preview text",
        )
        json_str = result.model_dump_json()
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["score"] == 0.7
        assert parsed["content"] == "preview text"

    def test_extra_fields_forbidden(self):
        """Extra fields are rejected."""
        with pytest.raises(ValidationError):
            EngineSearchResult(
                label="Test",
                score=0.5,
                result_type="node",
                id="n1",
                unknown="field",
            )


@pytest.mark.unit
@pytest.mark.core
class TestTokenUsage:
    """Test TokenUsage model."""

    def test_create_with_all_fields(self):
        usage = TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30)
        assert usage.input_tokens == 10
        assert usage.output_tokens == 20
        assert usage.total_tokens == 30

    def test_optional_cost(self):
        usage = TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10, cost_usd=0.001)
        assert usage.cost_usd == 0.001

    def test_cost_defaults_to_none(self):
        usage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)
        assert usage.cost_usd is None


@pytest.mark.unit
@pytest.mark.core
class TestLLMChatResponse:
    """Test LLMChatResponse model."""

    def test_create_non_streaming(self):
        response = LLMChatResponse(
            content="Hello world",
            provider="openai",
            is_stream=False,
            usage=TokenUsage(input_tokens=5, output_tokens=10, total_tokens=15),
        )
        assert response.content == "Hello world"
        assert response.provider == "openai"
        assert response.is_stream is False
        assert response.usage.total_tokens == 15
        assert response.tool_calls is None
        assert response.thinking is None
        assert response.stream is None

    def test_create_streaming(self):
        response = LLMChatResponse(
            content="",
            provider="ollama",
            is_stream=True,
        )
        assert response.is_stream is True
        assert response.usage is None

    def test_model_dump_json(self):
        response = LLMChatResponse(
            content="test",
            provider="openai",
            is_stream=False,
            usage=TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3),
        )
        json_str = response.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["content"] == "test"
        assert parsed["usage"]["total_tokens"] == 3


@pytest.mark.unit
@pytest.mark.core
class TestEmbedResult:
    """Test EmbedResult model."""

    def test_create_with_all_fields(self):
        result = EmbedResult(
            embedding=[0.1, 0.2, 0.3],
            provider="openai",
        )
        assert result.embedding == [0.1, 0.2, 0.3]
        assert result.provider == "openai"

    def test_optional_usage(self):
        result = EmbedResult(
            embedding=[0.1],
            provider="ollama",
            usage=TokenUsage(input_tokens=5, output_tokens=0, total_tokens=5),
        )
        assert result.usage.input_tokens == 5


@pytest.mark.unit
@pytest.mark.core
class TestBatchEmbedResult:
    """Test BatchEmbedResult model."""

    def test_create(self):
        result = BatchEmbedResult(
            embeddings=[[0.1, 0.2], [0.3, 0.4]],
            total=2,
            failed=0,
            provider="openai",
        )
        assert len(result.embeddings) == 2
        assert result.total == 2
        assert result.failed == 0

    def test_with_failures(self):
        result = BatchEmbedResult(
            embeddings=[[0.1], []],
            total=2,
            failed=1,
            provider="ollama",
        )
        assert result.failed == 1


@pytest.mark.unit
@pytest.mark.core
class TestToolResult:
    """Test ToolResult model."""

    def test_create(self):
        result = ToolResult(result={"data": "test"}, tool_name="search_graph")
        assert result.result == {"data": "test"}
        assert result.tool_name == "search_graph"


@pytest.mark.unit
@pytest.mark.core
class TestHealthResult:
    """Test HealthResult model."""

    def test_healthy(self):
        result = HealthResult(
            status="healthy",
            provider="openai",
            model="gpt-4.1",
            response_time_ms=150,
        )
        assert result.status == "healthy"
        assert result.error is None

    def test_unhealthy(self):
        result = HealthResult(
            status="unhealthy",
            provider="ollama",
            error="Connection refused",
        )
        assert result.status == "unhealthy"
        assert result.error == "Connection refused"


@pytest.mark.unit
@pytest.mark.core
class TestHealthReport:
    """Test HealthReport model."""

    def test_create(self):
        report = HealthReport(
            chat=HealthResult(status="healthy", provider="openai"),
        )
        assert report.chat.status == "healthy"


@pytest.mark.unit
@pytest.mark.core
class TestEngineSearchResultSnippet:
    """Test EngineSearchResult snippet property."""

    def test_node_snippet_is_label(self):
        result = EngineSearchResult(
            label="Ada Lovelace",
            score=0.9,
            result_type="node",
            id="n1",
        )
        assert result.snippet == "Ada Lovelace"

    def test_chunk_snippet_is_content(self):
        result = EngineSearchResult(
            label="Ada Lovelace was...",
            score=0.8,
            result_type="chunk",
            id="c1",
            content="Ada Lovelace was a mathematician who wrote the first algorithm.",
        )
        assert result.snippet == "Ada Lovelace was a mathematician who wrote the first algorithm."

    def test_chunk_snippet_without_content_falls_back_to_label(self):
        result = EngineSearchResult(
            label="chunk preview",
            score=0.7,
            result_type="chunk",
            id="c2",
        )
        assert result.snippet == "chunk preview"


@pytest.mark.unit
@pytest.mark.core
class TestIndexingResult:
    """Test IndexingResult model."""

    def test_create_with_all_fields(self):
        """IndexingResult can be created with all fields."""
        result = IndexingResult(
            chunks_count=42,
            embedding_model="snowflake-arctic-embed2",
            embedding_dimensions=1024,
        )
        assert result.chunks_count == 42
        assert result.embedding_model == "snowflake-arctic-embed2"
        assert result.embedding_dimensions == 1024

    def test_model_dump_returns_dict(self):
        """model_dump() returns a dict."""
        result = IndexingResult(
            chunks_count=10,
            embedding_model="test-model",
            embedding_dimensions=768,
        )
        dumped = result.model_dump()
        assert isinstance(dumped, dict)
        assert dumped["chunks_count"] == 10

    def test_extra_fields_forbidden(self):
        """Extra fields are rejected."""
        with pytest.raises(ValidationError):
            IndexingResult(
                chunks_count=1,
                embedding_model="m",
                embedding_dimensions=1,
                unknown_field="bad",
            )


@pytest.mark.unit
@pytest.mark.core
class TestRebuildResult:
    """Test RebuildResult model."""

    def test_create_with_all_fields(self):
        """RebuildResult can be created with all fields."""
        result = RebuildResult(
            total_nodes=100,
            nodes_with_embeddings=85,
            chunks_indexed=420,
        )
        assert result.total_nodes == 100
        assert result.nodes_with_embeddings == 85
        assert result.chunks_indexed == 420

    def test_model_dump_returns_dict(self):
        """model_dump() returns a dict."""
        result = RebuildResult(
            total_nodes=0,
            nodes_with_embeddings=0,
            chunks_indexed=0,
        )
        dumped = result.model_dump()
        assert isinstance(dumped, dict)
        assert dumped["total_nodes"] == 0

    def test_extra_fields_forbidden(self):
        """Extra fields are rejected."""
        with pytest.raises(ValidationError):
            RebuildResult(
                total_nodes=0,
                nodes_with_embeddings=0,
                chunks_indexed=0,
                extra="bad",
            )

# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for summarize tool schema registration."""

from chaoscypher_core.services.workflows.tools.engine.schema_registry import (
    TOOL_SCHEMAS,
    get_essential_tool_schemas,
)


class TestSummarizeSchema:
    """Test summarize tool schema exists and is well-formed."""

    def test_summarize_schema_exists(self):
        """TOOL_SCHEMAS should contain a 'summarize' entry."""
        assert "summarize" in TOOL_SCHEMAS

    def test_summarize_schema_has_required_fields(self):
        """Summarize schema should have function name, description, and parameters."""
        schema = TOOL_SCHEMAS["summarize"]
        assert schema["type"] == "function"
        func = schema["function"]
        assert func["name"] == "summarize"
        assert "description" in func
        assert "parameters" in func

    def test_summarize_requires_query(self):
        """Summarize should require 'query' parameter."""
        params = TOOL_SCHEMAS["summarize"]["function"]["parameters"]
        assert "query" in params["properties"]
        assert "query" in params["required"]

    def test_summarize_has_optional_source_ids(self):
        """Summarize should have optional 'source_ids' parameter."""
        params = TOOL_SCHEMAS["summarize"]["function"]["parameters"]
        assert "source_ids" in params["properties"]
        assert "source_ids" not in params.get("required", [])

    def test_summarize_in_essential_tools(self):
        """Summarize should be retrievable via get_essential_tool_schemas."""
        schemas = get_essential_tool_schemas(["summarize"])
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "summarize"

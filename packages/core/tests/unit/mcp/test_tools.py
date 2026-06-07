# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for MCP settings and tool definitions."""

import pytest
from pydantic import ValidationError

from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS, get_tools_for_mode
from chaoscypher_core.settings import EngineSettings, MCPSettings


class TestMCPSettings:
    """MCPSettings default values and validation."""

    def test_defaults(self):
        s = MCPSettings()
        assert s.mode == "read"
        assert s.auto_extract is False

    def test_write_mode(self):
        s = MCPSettings(mode="write")
        assert s.mode == "write"

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError):
            MCPSettings(mode="invalid")

    def test_engine_settings_includes_mcp(self):
        es = EngineSettings()
        assert hasattr(es, "mcp")
        assert es.mcp.mode == "read"


class TestToolDefinitions:
    """Tool definitions completeness and structure."""

    def test_total_tool_count(self):
        assert len(TOOL_DEFINITIONS) == 31

    def test_all_tools_have_required_fields(self):
        for tool in TOOL_DEFINITIONS:
            assert tool.name, f"Tool missing name: {tool}"
            assert tool.description, f"Tool {tool.name} missing description"
            assert isinstance(tool.input_schema, dict), f"Tool {tool.name} missing schema"
            assert isinstance(tool.write_only, bool), f"Tool {tool.name} missing write_only"

    def test_read_mode_excludes_write_tools(self):
        read_tools = get_tools_for_mode("read")
        write_names = {t.name for t in TOOL_DEFINITIONS if t.write_only}
        read_names = {t.name for t in read_tools}
        assert write_names.isdisjoint(read_names)

    def test_write_mode_includes_all_tools(self):
        write_tools = get_tools_for_mode("write")
        assert len(write_tools) == 31

    def test_read_tool_count(self):
        read_tools = get_tools_for_mode("read")
        assert len(read_tools) == 19

    def test_write_tool_count(self):
        write_only = [t for t in TOOL_DEFINITIONS if t.write_only]
        assert len(write_only) == 12

    def test_new_tools_present(self):
        names = {t.name for t in TOOL_DEFINITIONS}
        assert "get_summary_context" in names
        assert "get_document_status" in names
        assert "add_document" in names
        assert "remove_document" in names

    def test_schemas_have_type_object(self):
        for tool in TOOL_DEFINITIONS:
            assert tool.input_schema.get("type") == "object", (
                f"Tool {tool.name} schema must have type: object"
            )


class TestConfirmExtractionTool:
    """The confirm_extraction MCP tool definition."""

    def test_confirm_extraction_is_registered(self):
        from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS

        names = {t.name for t in TOOL_DEFINITIONS}
        assert "confirm_extraction" in names

    def test_confirm_extraction_is_write_only(self):
        from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS

        tool = next(t for t in TOOL_DEFINITIONS if t.name == "confirm_extraction")
        assert tool.write_only is True

    def test_confirm_extraction_hidden_in_read_mode(self):
        from chaoscypher_core.mcp.tools import get_tools_for_mode

        read_names = {t.name for t in get_tools_for_mode("read")}
        assert "confirm_extraction" not in read_names
        write_names = {t.name for t in get_tools_for_mode("write")}
        assert "confirm_extraction" in write_names

    def test_confirm_extraction_schema_requires_file_id(self):
        from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS

        tool = next(t for t in TOOL_DEFINITIONS if t.name == "confirm_extraction")
        schema = tool.input_schema
        assert schema["required"] == ["file_id"]
        props = schema["properties"]
        assert props["file_id"]["type"] == "string"
        # Optional domain override + full extraction option overrides.
        assert "domain" in props
        assert "analysis_depth" in props
        assert "filtering_mode" in props
        assert "enable_direction_correction" in props
        assert "protect_orphans" in props
        assert "enable_inverse_relationships" in props
        assert "max_entity_degree_override" in props


class TestAddDocumentAutoConfirm:
    """add_document gains an auto_confirm bypass flag."""

    def test_add_document_schema_has_auto_confirm(self):
        from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS

        tool = next(t for t in TOOL_DEFINITIONS if t.name == "add_document")
        props = tool.input_schema["properties"]
        assert "auto_confirm" in props
        assert props["auto_confirm"]["type"] == "boolean"
        assert props["auto_confirm"]["default"] is False


class TestMCPSettingsConfirmationDefault:
    """MCPSettings declares confirmation_required_default."""

    def test_field_defaults_true(self):
        from chaoscypher_core.settings import MCPSettings

        s = MCPSettings()
        assert s.confirmation_required_default is True

    def test_field_overridable(self):
        from chaoscypher_core.settings import MCPSettings

        s = MCPSettings(confirmation_required_default=False)
        assert s.confirmation_required_default is False

    def test_extra_forbid_preserved(self):
        import pytest
        from pydantic import ValidationError

        from chaoscypher_core.settings import MCPSettings

        with pytest.raises(ValidationError):
            MCPSettings(unknown_field=1)


class TestWaitForDocumentDocsReconciled:
    """wait_for_document docs point parked-doc pollers at get_document_status."""

    def test_description_mentions_get_document_status_for_parked(self):
        from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS

        tool = next(t for t in TOOL_DEFINITIONS if t.name == "wait_for_document")
        assert "get_document_status" in tool.description
        assert "awaiting_confirmation" in tool.description

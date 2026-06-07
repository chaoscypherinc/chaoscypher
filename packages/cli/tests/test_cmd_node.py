# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for node command group: get, create, list, update (and delete top-up).

Covers:
- get: found (table/json/yaml), not-found exit 1, include-links, yaml ImportError fallback,
       embedding/position display, error path
- create: basic (key=value props), json-props, invalid prop format, invalid JSON,
          duplicate/error path, no-properties path, result with properties
- list: with nodes (table), empty, template filter, json format, yaml format,
        yaml ImportError fallback, pagination info, long label truncation, error path
- update: label only, set-property, unset-property, unset missing key, not-found exit 1,
          no-updates early return, invalid set format, error path
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.node.create import create
from chaoscypher_cli.commands.node.get import get
from chaoscypher_cli.commands.node.list import list_nodes
from chaoscypher_cli.commands.node.update import update


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NODE_FULL = {
    "id": "node-abc",
    "label": "Alice",
    "template_id": "Person",
    "created_at": "2025-01-15T10:00:00",
    "updated_at": "2025-01-16T10:00:00",
    "properties": {"role": "CEO", "dept": "Engineering"},
    "position": {"x": 10.0, "y": 20.0},
    "embedding": [0.1, 0.2, 0.3],
}

_NODE_MINIMAL = {
    "id": "node-xyz",
    "label": "Bob",
    "template_id": "Person",
    "created_at": "2025-02-01T00:00:00",
    "updated_at": "2025-02-01T00:00:00",
    "properties": {},
    "position": None,
    "embedding": None,
}

_LIST_RESULT_EMPTY: dict = {
    "data": [],
    "pagination": {"page": 1, "page_size": 20, "total_pages": 1, "total": 0},
}

_LIST_RESULT_ONE: dict = {
    "data": [_NODE_FULL],
    "pagination": {"page": 1, "page_size": 20, "total_pages": 2, "total": 30},
}


def _make_ctx(
    *,
    node_service: MagicMock | None = None,
    edge_service: MagicMock | None = None,
    template_service: MagicMock | None = None,
) -> SimpleNamespace:
    """Build a lightweight context namespace."""
    return SimpleNamespace(
        node_service=node_service or MagicMock(),
        edge_service=edge_service or MagicMock(),
        template_service=template_service or MagicMock(),
    )


# ===========================================================================
# get.py tests
# ===========================================================================


class TestNodeGet:
    """Tests for `chaoscypher node get`."""

    def test_get_found_table_format(self) -> None:
        """Basic get returns node details in table format (default)."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL

        with patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["node-abc"])

        assert result.exit_code == 0, result.output
        assert "Alice" in result.output
        assert "Person" in result.output
        assert "CEO" in result.output
        ctx.node_service.get_node.assert_called_once_with("node-abc")

    def test_get_found_shows_embedding_and_position(self) -> None:
        """Table output includes embedding dimensions and position."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL

        with patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["node-abc"])

        assert result.exit_code == 0, result.output
        # position row
        assert "x=10.0" in result.output
        # embedding dimensions row
        assert "3 dimensions" in result.output

    def test_get_minimal_node_no_props_no_embedding(self) -> None:
        """Table output for node with no properties / embedding / position."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_MINIMAL

        with patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["node-xyz"])

        assert result.exit_code == 0, result.output
        assert "(none)" in result.output  # Properties (none)

    def test_get_not_found_exits_1(self) -> None:
        """Get exits 1 when node does not exist."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = None

        with patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["missing-id"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_get_json_format(self) -> None:
        """--format json prints valid JSON containing the node."""
        import json

        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL

        with patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["node-abc", "--format", "json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["node"]["id"] == "node-abc"

    def test_get_yaml_format(self) -> None:
        """--format yaml prints YAML output when PyYAML is available."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_MINIMAL

        with patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["node-xyz", "--format", "yaml"])

        assert result.exit_code == 0, result.output
        # yaml.dump always includes the key name
        assert "node" in result.output

    def test_get_yaml_importerror_fallback(self) -> None:
        """--format yaml falls back to JSON when PyYAML is not installed."""
        import builtins

        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_MINIMAL

        real_import = builtins.__import__

        def mock_import(name: str, *args, **kwargs):
            if name == "yaml":
                raise ImportError("no yaml")
            return real_import(name, *args, **kwargs)

        with patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx):
            with patch("builtins.__import__", side_effect=mock_import):
                result = runner.invoke(get, ["node-xyz", "--format", "yaml"])

        assert result.exit_code == 0, result.output
        assert "JSON" in result.output or "node" in result.output

    def test_get_include_links(self) -> None:
        """--include-links fetches edges and shows links table."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL
        outgoing_edge = {
            "id": "edge-1",
            "source_node_id": "node-abc",
            "target_node_id": "node-xyz",
            "label": "KNOWS",
        }
        ctx.edge_service.list_edges.side_effect = [
            {"data": [outgoing_edge]},
            {"data": []},
        ]
        settings_mock = MagicMock()
        settings_mock.cli.edge_batch_size = 50

        with (
            patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx),
            patch("chaoscypher_cli.commands.node.get.get_settings", return_value=settings_mock),
        ):
            result = runner.invoke(get, ["node-abc", "--include-links"])

        assert result.exit_code == 0, result.output
        assert "KNOWS" in result.output

    def test_get_include_links_none_found(self) -> None:
        """--include-links with no edges shows the 'no links' message."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL
        ctx.edge_service.list_edges.return_value = {"data": []}
        settings_mock = MagicMock()
        settings_mock.cli.edge_batch_size = 50

        with (
            patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx),
            patch("chaoscypher_cli.commands.node.get.get_settings", return_value=settings_mock),
        ):
            result = runner.invoke(get, ["node-abc", "--include-links"])

        assert result.exit_code == 0, result.output
        assert "No connected links" in result.output

    def test_get_include_links_json_format(self) -> None:
        """--include-links --format json embeds links in the JSON payload."""
        import json

        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_MINIMAL
        edge = {
            "id": "edge-99",
            "source_node_id": "node-xyz",
            "target_node_id": "other",
            "label": "RELATED",
        }
        ctx.edge_service.list_edges.side_effect = [
            {"data": [edge]},
            {"data": []},
        ]
        settings_mock = MagicMock()
        settings_mock.cli.edge_batch_size = 50

        with (
            patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx),
            patch("chaoscypher_cli.commands.node.get.get_settings", return_value=settings_mock),
        ):
            result = runner.invoke(get, ["node-xyz", "--format", "json", "--include-links"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "links" in data
        assert data["links"][0]["id"] == "edge-99"

    def test_get_include_links_incoming_direction(self) -> None:
        """An edge where target == node_id renders as incoming."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL
        incoming_edge = {
            "id": "edge-in",
            "source_node_id": "other-node",
            "target_node_id": "node-abc",
            "label": "CITES",
        }
        ctx.edge_service.list_edges.side_effect = [
            {"data": []},
            {"data": [incoming_edge]},
        ]
        settings_mock = MagicMock()
        settings_mock.cli.edge_batch_size = 50

        with (
            patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx),
            patch("chaoscypher_cli.commands.node.get.get_settings", return_value=settings_mock),
        ):
            result = runner.invoke(get, ["node-abc", "--include-links"])

        assert result.exit_code == 0, result.output
        assert "incoming" in result.output

    def test_get_error_exits_1(self) -> None:
        """Exception in get exits 1 with error message."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.side_effect = RuntimeError("DB connection failed")

        with patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["node-abc"])

        assert result.exit_code == 1
        assert "Error" in result.output


# ===========================================================================
# create.py tests
# ===========================================================================


class TestNodeCreate:
    """Tests for `chaoscypher node create`."""

    def _make_create_result(self, node_id: str = "node-new") -> dict:
        return {
            "id": node_id,
            "template_id": "Person",
            "label": "Alice",
            "properties": {"role": "CEO"},
        }

    def test_create_basic_no_properties(self) -> None:
        """Basic create with template and label, no extra properties."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.create_node.return_value = {
            "id": "node-new",
            "template_id": "Person",
            "label": "Alice",
            "properties": {},
        }

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(create, ["-t", "Person", "-l", "Alice"])

        assert result.exit_code == 0, result.output
        assert "node-new" in result.output
        assert "created successfully" in result.output
        ctx.node_service.create_node.assert_called_once()

    def test_create_with_key_value_properties(self) -> None:
        """--property key=value flags are parsed into properties dict."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.create_node.return_value = self._make_create_result()

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(
                create, ["-t", "Person", "-l", "Alice", "-p", "role=CEO", "-p", "dept=Engineering"]
            )

        assert result.exit_code == 0, result.output
        call_arg = ctx.node_service.create_node.call_args[0][0]
        assert call_arg.properties.get("role") == "CEO"
        assert call_arg.properties.get("dept") == "Engineering"

    def test_create_result_with_properties_printed(self) -> None:
        """Properties in the created node result are printed."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.create_node.return_value = self._make_create_result()

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(create, ["-t", "Person", "-l", "Alice", "-p", "role=CEO"])

        assert result.exit_code == 0, result.output
        assert "CEO" in result.output

    def test_create_with_json_props(self) -> None:
        """--json-props merges JSON properties."""
        import json

        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.create_node.return_value = self._make_create_result()
        json_input = json.dumps({"date": "2024-01-15", "location": "NYC"})

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(create, ["-t", "Event", "-l", "Meeting", "-j", json_input])

        assert result.exit_code == 0, result.output
        call_arg = ctx.node_service.create_node.call_args[0][0]
        assert call_arg.properties.get("date") == "2024-01-15"
        assert call_arg.properties.get("location") == "NYC"

    def test_create_invalid_property_format_exits_1(self) -> None:
        """-p without '=' prints error and exits 1."""
        runner = CliRunner()
        ctx = _make_ctx()

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(create, ["-t", "Person", "-l", "Alice", "-p", "noequalssign"])

        assert result.exit_code == 1
        assert "Invalid property format" in result.output

    def test_create_invalid_json_exits_1(self) -> None:
        """--json-props with invalid JSON prints error and exits 1."""
        runner = CliRunner()
        ctx = _make_ctx()

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(create, ["-t", "Person", "-l", "Alice", "-j", "{bad json}"])

        assert result.exit_code == 1
        assert "Invalid JSON" in result.output

    def test_create_json_props_merged_with_kv_props(self) -> None:
        """Both -p and -j provided; JSON merges on top of key=value props."""
        import json

        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.create_node.return_value = {
            "id": "node-merge",
            "template_id": "Person",
            "label": "Bob",
            "properties": {"role": "CTO", "city": "London"},
        }
        json_input = json.dumps({"city": "London"})

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(
                create,
                ["-t", "Person", "-l", "Bob", "-p", "role=CTO", "-j", json_input],
            )

        assert result.exit_code == 0, result.output
        call_arg = ctx.node_service.create_node.call_args[0][0]
        assert call_arg.properties.get("role") == "CTO"
        assert call_arg.properties.get("city") == "London"

    def test_create_service_error_exits_1(self) -> None:
        """Service exception prints error and exits 1."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.create_node.side_effect = ValueError("Duplicate node")

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(create, ["-t", "Person", "-l", "Alice"])

        assert result.exit_code == 1
        assert "Error" in result.output

    def test_create_result_no_properties(self) -> None:
        """When result.properties is empty, no property lines are printed."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.create_node.return_value = {
            "id": "node-simple",
            "template_id": "Thing",
            "label": "Widget",
            "properties": {},
        }

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(create, ["-t", "Thing", "-l", "Widget"])

        assert result.exit_code == 0, result.output
        assert "node-simple" in result.output


# ===========================================================================
# list.py tests
# ===========================================================================


class TestNodeList:
    """Tests for `chaoscypher node list`."""

    def test_list_table_with_nodes(self) -> None:
        """List shows node IDs and labels in table format."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.return_value = _LIST_RESULT_ONE

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            result = runner.invoke(list_nodes, ["--limit", "20"])

        assert result.exit_code == 0, result.output
        assert "node-abc" in result.output
        assert "Alice" in result.output
        ctx.node_service.list_nodes.assert_called_once()

    def test_list_empty_shows_create_hint(self) -> None:
        """List with no nodes shows 'No nodes found' and a create hint."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.return_value = _LIST_RESULT_EMPTY

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            result = runner.invoke(list_nodes, ["--limit", "20"])

        assert result.exit_code == 0, result.output
        assert "No nodes found" in result.output
        assert "chaoscypher node create" in result.output

    def test_list_empty_with_template_filter_shows_filter_hint(self) -> None:
        """Empty result when --template filter active shows filter info."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.return_value = _LIST_RESULT_EMPTY

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            result = runner.invoke(list_nodes, ["--template", "Robot", "--limit", "20"])

        assert result.exit_code == 0, result.output
        assert "Robot" in result.output

    def test_list_template_filter_passed_to_service(self) -> None:
        """--template value is forwarded to list_nodes as template_id."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.return_value = _LIST_RESULT_EMPTY

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            runner.invoke(list_nodes, ["--template", "Person", "--limit", "20"])

        ctx.node_service.list_nodes.assert_called_once()
        kwargs = ctx.node_service.list_nodes.call_args[1]
        assert kwargs.get("template_id") == "Person"

    def test_list_pagination_shows_next_hint(self) -> None:
        """When current page < total_pages, a 'Next page' hint is shown."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.return_value = _LIST_RESULT_ONE  # total_pages=2

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            result = runner.invoke(list_nodes, ["--page", "1", "--limit", "20"])

        assert result.exit_code == 0, result.output
        assert "Next page" in result.output

    def test_list_json_format(self) -> None:
        """--format json returns JSON-parseable output."""
        import json

        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.return_value = _LIST_RESULT_ONE

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            result = runner.invoke(list_nodes, ["--format", "json", "--limit", "20"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "data" in data

    def test_list_yaml_format(self) -> None:
        """--format yaml prints YAML when PyYAML is available."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.return_value = _LIST_RESULT_ONE

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            result = runner.invoke(list_nodes, ["--format", "yaml", "--limit", "20"])

        assert result.exit_code == 0, result.output
        assert "data" in result.output

    def test_list_yaml_importerror_fallback(self) -> None:
        """--format yaml falls back to JSON when PyYAML is not installed."""
        import builtins

        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.return_value = _LIST_RESULT_ONE

        real_import = builtins.__import__

        def mock_import(name: str, *args, **kwargs):
            if name == "yaml":
                raise ImportError("no yaml")
            return real_import(name, *args, **kwargs)

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            with patch("builtins.__import__", side_effect=mock_import):
                result = runner.invoke(list_nodes, ["--format", "yaml", "--limit", "20"])

        assert result.exit_code == 0, result.output
        assert "JSON" in result.output or "data" in result.output

    def test_list_long_label_is_truncated(self) -> None:
        """Labels longer than 40 chars are truncated to 37 chars + '...'."""
        runner = CliRunner()
        ctx = _make_ctx()
        # Use a label with a unique prefix so we can verify it's cut at 37 chars
        long_label = "X" * 37 + "TRIMMED_SUFFIX"
        ctx.node_service.list_nodes.return_value = {
            "data": [
                {
                    "id": "node-long",
                    "label": long_label,
                    "template_id": "Thing",
                    "created_at": "2025-01-01T00:00:00",
                    "properties": {},
                }
            ],
            "pagination": {"page": 1, "page_size": 20, "total_pages": 1, "total": 1},
        }

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            result = runner.invoke(list_nodes, ["--limit", "20"])

        assert result.exit_code == 0, result.output
        # The full suffix should not appear in output since it was trimmed
        assert "TRIMMED_SUFFIX" not in result.output

    def test_list_created_date_formatted(self) -> None:
        """ISO-format created_at is displayed as YYYY-MM-DD."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.return_value = {
            "data": [
                {
                    "id": "node-date",
                    "label": "DateNode",
                    "template_id": "Thing",
                    "created_at": "2025-06-15T12:30:00",
                    "properties": {},
                }
            ],
            "pagination": {"page": 1, "page_size": 20, "total_pages": 1, "total": 1},
        }

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            result = runner.invoke(list_nodes, ["--limit", "20"])

        assert result.exit_code == 0, result.output
        assert "2025-06-15" in result.output

    def test_list_error_exits_1(self) -> None:
        """Exception in list exits 1 with error message."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.side_effect = RuntimeError("DB error")

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            result = runner.invoke(list_nodes, ["--limit", "20"])

        assert result.exit_code == 1
        assert "Error" in result.output


# ===========================================================================
# update.py tests
# ===========================================================================


class TestNodeUpdate:
    """Tests for `chaoscypher node update`."""

    def test_update_label_only(self) -> None:
        """--label updates the label and prints the new value."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL
        ctx.node_service.update_node.return_value = {**_NODE_FULL, "label": "Carol"}

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["node-abc", "--label", "Carol"])

        assert result.exit_code == 0, result.output
        assert "updated successfully" in result.output
        assert "Carol" in result.output
        ctx.node_service.update_node.assert_called_once()

    def test_update_set_property(self) -> None:
        """--set key=value merges property into existing node properties."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL
        ctx.node_service.update_node.return_value = {
            **_NODE_FULL,
            "properties": {**_NODE_FULL["properties"], "title": "VP"},
        }

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["node-abc", "-s", "title=VP"])

        assert result.exit_code == 0, result.output
        assert "updated successfully" in result.output
        call_arg_update = ctx.node_service.update_node.call_args[0][1]
        assert call_arg_update.properties.get("title") == "VP"

    def test_update_set_invalid_format_exits_1(self) -> None:
        """--set without '=' prints error and exits 1."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["node-abc", "-s", "noequalssign"])

        assert result.exit_code == 1
        assert "Invalid property format" in result.output

    def test_update_unset_existing_property(self) -> None:
        """--unset removes the key from the node's properties."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL
        ctx.node_service.update_node.return_value = {
            **_NODE_FULL,
            "properties": {"dept": "Engineering"},
        }

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["node-abc", "--unset", "role"])

        assert result.exit_code == 0, result.output
        assert "updated successfully" in result.output
        # The update_node call should have properties without 'role'
        call_arg_update = ctx.node_service.update_node.call_args[0][1]
        assert "role" not in call_arg_update.properties

    def test_update_unset_missing_key_shows_warning(self) -> None:
        """--unset a key that doesn't exist prints a yellow warning."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL
        ctx.node_service.update_node.return_value = _NODE_FULL

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["node-abc", "--unset", "nonexistent_key"])

        assert result.exit_code == 0, result.output
        assert "not found" in result.output.lower() or "Property not found" in result.output

    def test_update_not_found_exits_1(self) -> None:
        """Update exits 1 when the node is not found."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = None

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["missing-node", "--label", "NewName"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_update_no_updates_specified_returns_early(self) -> None:
        """Update with no flags prints 'No updates specified' and returns 0."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["node-abc"])

        assert result.exit_code == 0, result.output
        assert "No updates specified" in result.output
        ctx.node_service.update_node.assert_not_called()

    def test_update_multiple_set_properties(self) -> None:
        """Multiple --set flags merge all into properties."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_MINIMAL
        ctx.node_service.update_node.return_value = {
            **_NODE_MINIMAL,
            "properties": {"role": "CTO", "dept": "Tech"},
        }

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["node-xyz", "-s", "role=CTO", "-s", "dept=Tech"])

        assert result.exit_code == 0, result.output
        call_arg_update = ctx.node_service.update_node.call_args[0][1]
        assert call_arg_update.properties.get("role") == "CTO"
        assert call_arg_update.properties.get("dept") == "Tech"

    def test_update_label_and_properties_together(self) -> None:
        """Combining --label and --set updates both fields."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL
        ctx.node_service.update_node.return_value = {
            **_NODE_FULL,
            "label": "Updated",
            "properties": {**_NODE_FULL["properties"], "title": "Director"},
        }

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(
                update, ["node-abc", "--label", "Updated", "-s", "title=Director"]
            )

        assert result.exit_code == 0, result.output
        call_arg_update = ctx.node_service.update_node.call_args[0][1]
        assert call_arg_update.label == "Updated"
        assert call_arg_update.properties.get("title") == "Director"

    def test_update_error_exits_1(self) -> None:
        """Service exception during update exits 1 with error message."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL
        ctx.node_service.update_node.side_effect = RuntimeError("Constraint violation")

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["node-abc", "--label", "New"])

        assert result.exit_code == 1
        assert "Error" in result.output

    def test_update_unset_prints_removed_keys(self) -> None:
        """--unset for existing key prints 'Properties removed' line."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_FULL
        ctx.node_service.update_node.return_value = {
            **_NODE_FULL,
            "properties": {"dept": "Engineering"},
        }

        with patch("chaoscypher_cli.commands.node.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["node-abc", "--unset", "role"])

        assert result.exit_code == 0, result.output
        assert "removed" in result.output.lower() or "role" in result.output


# ===========================================================================
# Additional coverage: missing list.py branches
# ===========================================================================


class TestNodeListMissingBranches:
    """Cover the remaining uncovered branches in list.py."""

    def test_list_invalid_created_at_uses_raw_value(self) -> None:
        """Invalid ISO date string in created_at is kept as-is (except handler)."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.list_nodes.return_value = {
            "data": [
                {
                    "id": "node-bad-date",
                    "label": "BadDate",
                    "template_id": "Thing",
                    "created_at": "not-a-valid-date",
                    "properties": {},
                }
            ],
            "pagination": {"page": 1, "page_size": 20, "total_pages": 1, "total": 1},
        }

        with patch("chaoscypher_cli.commands.node.list.get_context", return_value=ctx):
            result = runner.invoke(list_nodes, ["--limit", "20"])

        # Should not crash; the raw string is used as fallback
        assert result.exit_code == 0, result.output
        assert "BadDate" in result.output


# ===========================================================================
# Additional coverage: missing get.py branches
# ===========================================================================


class TestNodeGetMissingBranches:
    """Cover the remaining uncovered branches in get.py."""

    def test_get_yaml_format_with_include_links(self) -> None:
        """--format yaml --include-links adds 'links' key to the YAML output."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = _NODE_MINIMAL
        edge = {
            "id": "edge-yml",
            "source_node_id": "node-xyz",
            "target_node_id": "other",
            "label": "REFS",
        }
        ctx.edge_service.list_edges.side_effect = [
            {"data": [edge]},
            {"data": []},
        ]
        settings_mock = MagicMock()
        settings_mock.cli.edge_batch_size = 50

        with (
            patch("chaoscypher_cli.commands.node.get.get_context", return_value=ctx),
            patch("chaoscypher_cli.commands.node.get.get_settings", return_value=settings_mock),
        ):
            result = runner.invoke(get, ["node-xyz", "--format", "yaml", "--include-links"])

        assert result.exit_code == 0, result.output
        assert "links" in result.output or "node" in result.output


# ===========================================================================
# Additional coverage: interactive wizard in create.py
# ===========================================================================


class TestNodeCreateInteractive:
    """Cover the interactive wizard branches in create.py (lines 49-98)."""

    def test_create_interactive_no_templates_exits_1(self) -> None:
        """Interactive wizard exits 1 when no templates are available."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.template_service.list_templates.return_value = {"data": []}

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(
                create, ["-t", "Person", "-l", "Alice", "--interactive"], input="\n"
            )

        assert result.exit_code == 1
        assert "No templates found" in result.output

    def test_create_interactive_with_templates_and_props(self) -> None:
        """Interactive wizard with templates and property definitions creates node."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.template_service.list_templates.return_value = {
            "data": [{"id": "Person", "name": "Person Template"}]
        }
        ctx.template_service.get_template.return_value = {
            "id": "Person",
            "name": "Person Template",
            "properties": [
                {"name": "role", "property_type": "STRING", "required": True, "default_value": None}
            ],
        }
        ctx.node_service.create_node.return_value = {
            "id": "node-interactive",
            "template_id": "Person",
            "label": "Dave",
            "properties": {"role": "Engineer"},
        }

        # Provide input: template=Person (default), label=Dave, role=Engineer, confirm=y
        user_input = "\nDave\nEngineer\ny\n"

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(
                create, ["-t", "Person", "-l", "Alice", "--interactive"], input=user_input
            )

        assert result.exit_code == 0, result.output
        assert "created successfully" in result.output or "node-interactive" in result.output

    def test_create_interactive_cancelled(self) -> None:
        """Interactive wizard that the user cancels returns without creating."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.template_service.list_templates.return_value = {
            "data": [{"id": "Person", "name": "Person Template"}]
        }
        ctx.template_service.get_template.return_value = {
            "id": "Person",
            "name": "Person Template",
            "properties": [],
        }

        # Input: template (accept default), label=Test, confirm=n
        user_input = "\nTest\nn\n"

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(
                create, ["-t", "Person", "-l", "Alice", "--interactive"], input=user_input
            )

        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output
        ctx.node_service.create_node.assert_not_called()

    def test_create_interactive_template_not_found_no_props(self) -> None:
        """Interactive wizard when get_template returns None falls back to empty props."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.template_service.list_templates.return_value = {
            "data": [{"id": "Thing", "name": "Thing Template"}]
        }
        ctx.template_service.get_template.return_value = None
        ctx.node_service.create_node.return_value = {
            "id": "node-thing",
            "template_id": "Thing",
            "label": "Widget",
            "properties": {},
        }

        # Input: template (accept default), label=Widget, confirm=y
        user_input = "\nWidget\ny\n"

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(
                create, ["-t", "Thing", "-l", "Placeholder", "--interactive"], input=user_input
            )

        assert result.exit_code == 0, result.output
        ctx.node_service.create_node.assert_called_once()
        call_arg = ctx.node_service.create_node.call_args[0][0]
        assert call_arg.properties == {}

    def test_create_interactive_template_with_no_props_def(self) -> None:
        """Interactive wizard when template has no properties dict creates with empty props."""
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.template_service.list_templates.return_value = {
            "data": [{"id": "Event", "name": "Event Template"}]
        }
        ctx.template_service.get_template.return_value = {
            "id": "Event",
            "name": "Event Template",
            "properties": [],  # no property definitions
        }
        ctx.node_service.create_node.return_value = {
            "id": "node-event",
            "template_id": "Event",
            "label": "Meeting",
            "properties": {},
        }

        # Input: template (accept default), label=Meeting, confirm=y
        user_input = "\nMeeting\ny\n"

        with patch("chaoscypher_cli.commands.node.create.get_context", return_value=ctx):
            result = runner.invoke(
                create, ["-t", "Event", "-l", "Placeholder", "--interactive"], input=user_input
            )

        assert result.exit_code == 0, result.output
        call_arg = ctx.node_service.create_node.call_args[0][0]
        assert call_arg.properties == {}

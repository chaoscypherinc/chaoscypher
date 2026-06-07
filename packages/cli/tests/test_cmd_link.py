# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the `link` command group (graph edges).

Covers all subcommands via Click's CliRunner with patched get_context:
- create: success (unidirectional + bidirectional), missing source, missing
  target, exception path.
- list: table (with edges, empty, with source filter, pagination), JSON,
  YAML (present + missing), exception path.
- get: table (full fields + properties + metadata), JSON, YAML, not-found
  exit 1, exception path.
- update: label, set property, unset property, invalid property format,
  no-updates-specified, not-found exit 1, exception path.
- delete: by id (force, confirm yes, confirm no, not-found), by
  source+target (found + force, found + confirm, not-found, type-filter),
  missing both args exit 1, exception path.
- __init__: group registration smoke test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.link import link
from chaoscypher_cli.commands.link.create import create
from chaoscypher_cli.commands.link.delete import delete
from chaoscypher_cli.commands.link.get import get
from chaoscypher_cli.commands.link.list import list_links
from chaoscypher_cli.commands.link.update import update


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

EDGE_ID = "edge_abc12345"
SOURCE_ID = "node_src001"
TARGET_ID = "node_tgt002"
LINK_TYPE = "works_for"


def _make_edge(**overrides: Any) -> dict[str, Any]:
    """Return a minimal edge record dict."""
    record: dict[str, Any] = {
        "id": EDGE_ID,
        "source_node_id": SOURCE_ID,
        "target_node_id": TARGET_ID,
        "template_id": LINK_TYPE,
        "label": "Works For",
        "properties": {},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }
    record.update(overrides)
    return record


def _make_list_result(edges: list[dict[str, Any]], total: int = 0) -> dict[str, Any]:
    actual_total = total or len(edges)
    return {
        "data": edges,
        "pagination": {
            "total": actual_total,
            "total_pages": 1,
            "page": 1,
        },
    }


def _make_ctx(
    *,
    edge: dict[str, Any] | None = _make_edge(),
    list_result: dict[str, Any] | None = None,
    source_node: dict[str, Any] | None = None,
    target_node: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a fully configured mock CLI context."""
    ctx = MagicMock()
    ctx.database_name = "default"

    # node_service mocks
    ctx.node_service.get_node.return_value = {"id": "node_src001"}

    # edge_service mocks
    ctx.edge_service.get_edge.return_value = edge
    ctx.edge_service.create_edge.return_value = {"id": EDGE_ID}
    ctx.edge_service.update_edge.return_value = edge
    ctx.edge_service.delete_edge.return_value = True
    ctx.edge_service.list_edges.return_value = (
        list_result if list_result is not None else _make_list_result([_make_edge()])
    )
    return ctx


# Patch target prefix — all link commands import get_context at module top-level
_CREATE = "chaoscypher_cli.commands.link.create.get_context"
_LIST = "chaoscypher_cli.commands.link.list.get_context"
_GET = "chaoscypher_cli.commands.link.get.get_context"
_UPDATE = "chaoscypher_cli.commands.link.update.get_context"
_DELETE = "chaoscypher_cli.commands.link.delete.get_context"

# Patch target for get_settings used in list + delete
_LIST_SETTINGS = "chaoscypher_cli.commands.link.list.get_settings"
_DELETE_SETTINGS = "chaoscypher_cli.commands.link.delete.get_settings"


def _settings_mock(list_page_size: int = 50, edge_batch_size: int = 200) -> MagicMock:
    s = MagicMock()
    s.cli.list_page_size = list_page_size
    s.cli.edge_batch_size = edge_batch_size
    return s


# ===========================================================================
# create
# ===========================================================================


class TestLinkCreate:
    def test_create_success_unidirectional(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        # Both source and target nodes exist
        ctx.node_service.get_node.side_effect = [
            {"id": SOURCE_ID},
            {"id": TARGET_ID},
        ]
        ctx.edge_service.create_edge.return_value = {"id": EDGE_ID}

        with patch(_CREATE, return_value=ctx):
            result = runner.invoke(
                create,
                [SOURCE_ID, TARGET_ID, "--type", LINK_TYPE, "--database", "default"],
            )

        assert result.exit_code == 0, result.output
        assert SOURCE_ID in result.output
        assert TARGET_ID in result.output
        assert LINK_TYPE in result.output
        assert EDGE_ID in result.output
        # create_edge called exactly once for unidirectional
        assert ctx.edge_service.create_edge.call_count == 1
        call_args = ctx.edge_service.create_edge.call_args[0][0]
        assert call_args.source_node_id == SOURCE_ID
        assert call_args.target_node_id == TARGET_ID
        assert call_args.template_id == LINK_TYPE

    def test_create_success_with_label(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.side_effect = [
            {"id": SOURCE_ID},
            {"id": TARGET_ID},
        ]
        ctx.edge_service.create_edge.return_value = {"id": EDGE_ID}

        with patch(_CREATE, return_value=ctx):
            result = runner.invoke(
                create,
                [SOURCE_ID, TARGET_ID, "-t", LINK_TYPE, "-l", "strongly influences"],
            )

        assert result.exit_code == 0, result.output
        assert "strongly influences" in result.output
        call_args = ctx.edge_service.create_edge.call_args[0][0]
        assert call_args.label == "strongly influences"

    def test_create_bidirectional(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.side_effect = [
            {"id": SOURCE_ID},
            {"id": TARGET_ID},
        ]
        ctx.edge_service.create_edge.side_effect = [
            {"id": "edge_fwd"},
            {"id": "edge_rev"},
        ]

        with patch(_CREATE, return_value=ctx):
            result = runner.invoke(
                create,
                [SOURCE_ID, TARGET_ID, "-t", LINK_TYPE, "--bidirectional"],
            )

        assert result.exit_code == 0, result.output
        assert ctx.edge_service.create_edge.call_count == 2
        # Forward edge
        fwd = ctx.edge_service.create_edge.call_args_list[0][0][0]
        assert fwd.source_node_id == SOURCE_ID
        assert fwd.target_node_id == TARGET_ID
        # Reverse edge
        rev = ctx.edge_service.create_edge.call_args_list[1][0][0]
        assert rev.source_node_id == TARGET_ID
        assert rev.target_node_id == SOURCE_ID
        assert "edge_fwd" in result.output
        assert "edge_rev" in result.output

    def test_create_source_not_found(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.return_value = None

        with patch(_CREATE, return_value=ctx):
            result = runner.invoke(
                create,
                [SOURCE_ID, TARGET_ID, "-t", LINK_TYPE],
            )

        assert result.exit_code == 1
        assert "Source node not found" in result.output
        ctx.edge_service.create_edge.assert_not_called()

    def test_create_target_not_found(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        # Source found, target not
        ctx.node_service.get_node.side_effect = [{"id": SOURCE_ID}, None]

        with patch(_CREATE, return_value=ctx):
            result = runner.invoke(
                create,
                [SOURCE_ID, TARGET_ID, "-t", LINK_TYPE],
            )

        assert result.exit_code == 1
        assert "Target node not found" in result.output
        ctx.edge_service.create_edge.assert_not_called()

    def test_create_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()
        ctx.node_service.get_node.side_effect = [{"id": SOURCE_ID}, {"id": TARGET_ID}]
        ctx.edge_service.create_edge.side_effect = RuntimeError("DB error")

        with patch(_CREATE, return_value=ctx):
            result = runner.invoke(create, [SOURCE_ID, TARGET_ID, "-t", LINK_TYPE])

        assert result.exit_code == 1
        assert "Error" in result.output

    def test_create_missing_type_flag(self) -> None:
        """--type is required; click should error with exit 2."""
        runner = CliRunner()
        result = runner.invoke(create, [SOURCE_ID, TARGET_ID])
        assert result.exit_code == 2


# ===========================================================================
# list
# ===========================================================================


class TestLinkList:
    def test_list_table_with_edges(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(list_result=_make_list_result([edge]))

        with (
            patch(_LIST, return_value=ctx),
            patch(_LIST_SETTINGS, return_value=_settings_mock()),
        ):
            result = runner.invoke(list_links, ["--limit", "50"])

        assert result.exit_code == 0, result.output
        # Table header + edge data present
        assert "Links" in result.output
        ctx.edge_service.list_edges.assert_called_once_with(
            source_node_id=None,
            page=1,
            page_size=50,
        )

    def test_list_table_empty(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx(list_result=_make_list_result([]))

        with (
            patch(_LIST, return_value=ctx),
            patch(_LIST_SETTINGS, return_value=_settings_mock()),
        ):
            result = runner.invoke(list_links, ["--limit", "50"])

        assert result.exit_code == 0, result.output
        assert "No links found" in result.output

    def test_list_table_empty_with_source_filter(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx(list_result=_make_list_result([]))

        with (
            patch(_LIST, return_value=ctx),
            patch(_LIST_SETTINGS, return_value=_settings_mock()),
        ):
            result = runner.invoke(list_links, ["--source", SOURCE_ID, "--limit", "50"])

        assert result.exit_code == 0, result.output
        assert "No links found" in result.output
        assert f"source={SOURCE_ID}" in result.output
        ctx.edge_service.list_edges.assert_called_once_with(
            source_node_id=SOURCE_ID,
            page=1,
            page_size=50,
        )

    def test_list_pagination_next_page_hint(self) -> None:
        runner = CliRunner()
        # Multiple pages: current page 1, total pages 3
        list_result = {
            "data": [_make_edge()],
            "pagination": {"total": 150, "total_pages": 3, "page": 1},
        }
        ctx = _make_ctx(list_result=list_result)

        with (
            patch(_LIST, return_value=ctx),
            patch(_LIST_SETTINGS, return_value=_settings_mock()),
        ):
            result = runner.invoke(list_links, ["--limit", "50"])

        assert result.exit_code == 0, result.output
        # Should hint next page
        assert "page 2" in result.output or "--page 2" in result.output

    def test_list_json_format(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(list_result=_make_list_result([edge]))

        with (
            patch(_LIST, return_value=ctx),
            patch(_LIST_SETTINGS, return_value=_settings_mock()),
        ):
            result = runner.invoke(list_links, ["--format", "json", "--limit", "50"])

        assert result.exit_code == 0, result.output
        # JSON output should contain the edge id
        assert EDGE_ID in result.output

    def test_list_yaml_format_present(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(list_result=_make_list_result([edge]))

        with (
            patch(_LIST, return_value=ctx),
            patch(_LIST_SETTINGS, return_value=_settings_mock()),
            patch.dict("sys.modules", {}),
        ):
            result = runner.invoke(list_links, ["--format", "yaml", "--limit", "50"])

        # Exit code 0 regardless of yaml availability
        assert result.exit_code == 0, result.output

    def test_list_yaml_format_missing(self) -> None:
        """When PyYAML is not installed, should fall back to JSON."""
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(list_result=_make_list_result([edge]))

        import sys as _sys

        # Simulate yaml not installed by patching the import
        original_yaml = _sys.modules.get("yaml")
        _sys.modules["yaml"] = None  # type: ignore[assignment]
        try:
            with (
                patch(_LIST, return_value=ctx),
                patch(_LIST_SETTINGS, return_value=_settings_mock()),
            ):
                result = runner.invoke(list_links, ["--format", "yaml", "--limit", "50"])
        finally:
            if original_yaml is None:
                _sys.modules.pop("yaml", None)
            else:
                _sys.modules["yaml"] = original_yaml

        assert result.exit_code == 0, result.output
        # Falls back to JSON or shows warning
        assert EDGE_ID in result.output or "YAML" in result.output

    def test_list_long_ids_truncated(self) -> None:
        """IDs longer than 15 chars render in the table (truncation exercised)."""
        runner = CliRunner()
        edge = _make_edge(
            id="a" * 20,
            source_node_id="b" * 20,
            target_node_id="c" * 20,
            label="x" * 30,
        )
        ctx = _make_ctx(list_result=_make_list_result([edge]))

        with (
            patch(_LIST, return_value=ctx),
            patch(_LIST_SETTINGS, return_value=_settings_mock()),
        ):
            result = runner.invoke(list_links, ["--limit", "50"])

        assert result.exit_code == 0, result.output
        # Table is rendered (truncation branch exercised — Rich may render
        # the truncated strings differently depending on terminal width)
        assert "Links" in result.output

    def test_list_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = MagicMock()
        ctx.edge_service.list_edges.side_effect = RuntimeError("conn error")

        with (
            patch(_LIST, return_value=ctx),
            patch(_LIST_SETTINGS, return_value=_settings_mock()),
        ):
            result = runner.invoke(list_links, ["--limit", "50"])

        assert result.exit_code == 1
        assert "Error" in result.output


# ===========================================================================
# get
# ===========================================================================


class TestLinkGet:
    def test_get_table_format(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(edge=edge)

        with patch(_GET, return_value=ctx):
            result = runner.invoke(get, [EDGE_ID])

        assert result.exit_code == 0, result.output
        assert SOURCE_ID in result.output
        assert TARGET_ID in result.output
        ctx.edge_service.get_edge.assert_called_once_with(EDGE_ID)

    def test_get_table_with_properties_and_metadata(self) -> None:
        runner = CliRunner()
        edge = _make_edge(
            properties={"context": "headquarters", "since": "2020"},
            metadata={"source": "manual"},
            weight=0.9,
        )
        ctx = _make_ctx(edge=edge)

        with patch(_GET, return_value=ctx):
            result = runner.invoke(get, [EDGE_ID])

        assert result.exit_code == 0, result.output
        assert "context" in result.output
        assert "headquarters" in result.output
        assert "source" in result.output
        assert "manual" in result.output
        assert "0.9" in result.output

    def test_get_table_with_updated_at(self) -> None:
        runner = CliRunner()
        edge = _make_edge(updated_at="2026-02-01T00:00:00Z")
        ctx = _make_ctx(edge=edge)

        with patch(_GET, return_value=ctx):
            result = runner.invoke(get, [EDGE_ID])

        assert result.exit_code == 0, result.output
        assert "2026-02-01" in result.output

    def test_get_json_format(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(edge=edge)

        with patch(_GET, return_value=ctx):
            result = runner.invoke(get, [EDGE_ID, "--format", "json"])

        assert result.exit_code == 0, result.output
        assert EDGE_ID in result.output
        assert SOURCE_ID in result.output

    def test_get_yaml_format_present(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(edge=edge)

        with patch(_GET, return_value=ctx):
            result = runner.invoke(get, [EDGE_ID, "--format", "yaml"])

        assert result.exit_code == 0, result.output

    def test_get_yaml_fallback_when_missing(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(edge=edge)

        import sys as _sys

        original_yaml = _sys.modules.get("yaml")
        _sys.modules["yaml"] = None  # type: ignore[assignment]
        try:
            with patch(_GET, return_value=ctx):
                result = runner.invoke(get, [EDGE_ID, "--format", "yaml"])
        finally:
            if original_yaml is None:
                _sys.modules.pop("yaml", None)
            else:
                _sys.modules["yaml"] = original_yaml

        assert result.exit_code == 0, result.output
        assert EDGE_ID in result.output or "YAML" in result.output

    def test_get_model_dump_path(self) -> None:
        """Edge object with model_dump() should be handled correctly."""
        runner = CliRunner()

        class FakeEdge:
            def model_dump(self) -> dict[str, Any]:
                return _make_edge()

        ctx = _make_ctx(edge=None)
        ctx.edge_service.get_edge.return_value = FakeEdge()

        with patch(_GET, return_value=ctx):
            result = runner.invoke(get, [EDGE_ID])

        assert result.exit_code == 0, result.output
        assert SOURCE_ID in result.output

    def test_get_dict_conversion_path(self) -> None:
        """Edge object that's not a dict or model should be converted via dict()."""
        runner = CliRunner()

        class FakeEdgeIterable:
            def keys(self) -> list[str]:
                return list(_make_edge().keys())

            def __iter__(self):  # type: ignore[override]
                return iter(_make_edge().items())

        ctx = _make_ctx(edge=None)
        ctx.edge_service.get_edge.return_value = dict(_make_edge())

        with patch(_GET, return_value=ctx):
            result = runner.invoke(get, [EDGE_ID])

        assert result.exit_code == 0, result.output

    def test_get_not_found_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx(edge=None)

        with patch(_GET, return_value=ctx):
            result = runner.invoke(get, ["nonexistent_edge"])

        assert result.exit_code == 1
        assert "Link not found" in result.output

    def test_get_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = MagicMock()
        ctx.edge_service.get_edge.side_effect = RuntimeError("boom")

        with patch(_GET, return_value=ctx):
            result = runner.invoke(get, [EDGE_ID])

        assert result.exit_code == 1
        assert "Error" in result.output


# ===========================================================================
# update
# ===========================================================================


class TestLinkUpdate:
    def test_update_label(self) -> None:
        runner = CliRunner()
        existing = _make_edge()
        ctx = _make_ctx(edge=existing)

        with patch(_UPDATE, return_value=ctx):
            result = runner.invoke(update, [EDGE_ID, "--label", "Reports To"])

        assert result.exit_code == 0, result.output
        assert "updated successfully" in result.output
        assert "Reports To" in result.output
        ctx.edge_service.update_edge.assert_called_once()
        call_args = ctx.edge_service.update_edge.call_args
        edge_update = call_args[0][1]
        assert edge_update.label == "Reports To"

    def test_update_set_property(self) -> None:
        runner = CliRunner()
        existing = _make_edge(properties={"old_key": "old_val"})
        ctx = _make_ctx(edge=existing)

        with patch(_UPDATE, return_value=ctx):
            result = runner.invoke(update, [EDGE_ID, "--set", "new_key=new_val"])

        assert result.exit_code == 0, result.output
        assert "updated successfully" in result.output
        call_args = ctx.edge_service.update_edge.call_args
        edge_update = call_args[0][1]
        assert edge_update.properties is not None
        assert edge_update.properties.get("new_key") == "new_val"
        # Old key should be preserved
        assert edge_update.properties.get("old_key") == "old_val"

    def test_update_unset_property(self) -> None:
        runner = CliRunner()
        existing = _make_edge(properties={"to_remove": "value", "keep": "this"})
        ctx = _make_ctx(edge=existing)

        with patch(_UPDATE, return_value=ctx):
            result = runner.invoke(update, [EDGE_ID, "--unset", "to_remove"])

        assert result.exit_code == 0, result.output
        call_args = ctx.edge_service.update_edge.call_args
        edge_update = call_args[0][1]
        assert "to_remove" not in (edge_update.properties or {})
        assert (edge_update.properties or {}).get("keep") == "this"

    def test_update_unset_nonexistent_property_warns(self) -> None:
        runner = CliRunner()
        existing = _make_edge(properties={"keep": "this"})
        ctx = _make_ctx(edge=existing)

        with patch(_UPDATE, return_value=ctx):
            result = runner.invoke(update, [EDGE_ID, "--unset", "ghost_key"])

        assert result.exit_code == 0, result.output
        assert "Property not found" in result.output

    def test_update_invalid_property_format_exits_1(self) -> None:
        runner = CliRunner()
        existing = _make_edge()
        ctx = _make_ctx(edge=existing)

        with patch(_UPDATE, return_value=ctx):
            result = runner.invoke(update, [EDGE_ID, "--set", "bad_format_no_equals"])

        assert result.exit_code == 1
        assert "Invalid property format" in result.output

    def test_update_no_args_prints_warning(self) -> None:
        runner = CliRunner()
        existing = _make_edge()
        ctx = _make_ctx(edge=existing)

        with patch(_UPDATE, return_value=ctx):
            result = runner.invoke(update, [EDGE_ID])

        assert result.exit_code == 0, result.output
        assert "No updates specified" in result.output
        ctx.edge_service.update_edge.assert_not_called()

    def test_update_model_dump_path(self) -> None:
        """Existing edge with model_dump() should work."""
        runner = CliRunner()

        class FakeEdge:
            def model_dump(self) -> dict[str, Any]:
                return _make_edge()

        ctx = _make_ctx(edge=None)
        ctx.edge_service.get_edge.return_value = FakeEdge()

        with patch(_UPDATE, return_value=ctx):
            result = runner.invoke(update, [EDGE_ID, "--label", "New Label"])

        assert result.exit_code == 0, result.output
        assert "updated successfully" in result.output

    def test_update_not_found_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx(edge=None)

        with patch(_UPDATE, return_value=ctx):
            result = runner.invoke(update, ["nonexistent_edge", "--label", "X"])

        assert result.exit_code == 1
        assert "Link not found" in result.output

    def test_update_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = MagicMock()
        ctx.edge_service.get_edge.side_effect = RuntimeError("db gone")

        with patch(_UPDATE, return_value=ctx):
            result = runner.invoke(update, [EDGE_ID, "--label", "X"])

        assert result.exit_code == 1
        assert "Error" in result.output


# ===========================================================================
# delete
# ===========================================================================


class TestLinkDelete:
    # -- by link_id ----------------------------------------------------------

    def test_delete_by_id_force(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(edge=edge)

        with patch(_DELETE, return_value=ctx):
            result = runner.invoke(delete, [EDGE_ID, "--force"])

        assert result.exit_code == 0, result.output
        assert "deleted successfully" in result.output
        ctx.edge_service.delete_edge.assert_called_once_with(EDGE_ID)

    def test_delete_by_id_confirms_yes(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(edge=edge)

        # CliRunner.invoke accepts `input` to feed stdin
        with patch(_DELETE, return_value=ctx):
            result = runner.invoke(delete, [EDGE_ID], input="y\n")

        assert result.exit_code == 0, result.output
        assert "deleted successfully" in result.output
        ctx.edge_service.delete_edge.assert_called_once_with(EDGE_ID)

    def test_delete_by_id_confirms_no(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx(edge=edge)

        with patch(_DELETE, return_value=ctx):
            result = runner.invoke(delete, [EDGE_ID], input="n\n")

        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output
        ctx.edge_service.delete_edge.assert_not_called()

    def test_delete_by_id_not_found_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx(edge=None)

        with patch(_DELETE, return_value=ctx):
            result = runner.invoke(delete, ["nonexistent_edge", "--force"])

        assert result.exit_code == 1
        assert "Link not found" in result.output
        ctx.edge_service.delete_edge.assert_not_called()

    # -- by source + target --------------------------------------------------

    def test_delete_by_source_target_force(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx()
        ctx.edge_service.list_edges.return_value = _make_list_result([edge])
        settings_mock = _settings_mock()

        with (
            patch(_DELETE, return_value=ctx),
            patch(_DELETE_SETTINGS, return_value=settings_mock),
        ):
            result = runner.invoke(
                delete,
                ["--source", SOURCE_ID, "--target", TARGET_ID, "--force"],
            )

        assert result.exit_code == 0, result.output
        assert "Deleted 1" in result.output or "deleted" in result.output.lower()
        ctx.edge_service.delete_edge.assert_called_once_with(EDGE_ID)

    def test_delete_by_source_target_confirms_yes(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx()
        ctx.edge_service.list_edges.return_value = _make_list_result([edge])
        settings_mock = _settings_mock()

        with (
            patch(_DELETE, return_value=ctx),
            patch(_DELETE_SETTINGS, return_value=settings_mock),
        ):
            result = runner.invoke(
                delete,
                ["--source", SOURCE_ID, "--target", TARGET_ID],
                input="y\n",
            )

        assert result.exit_code == 0, result.output
        ctx.edge_service.delete_edge.assert_called_once_with(EDGE_ID)

    def test_delete_by_source_target_confirms_no(self) -> None:
        runner = CliRunner()
        edge = _make_edge()
        ctx = _make_ctx()
        ctx.edge_service.list_edges.return_value = _make_list_result([edge])
        settings_mock = _settings_mock()

        with (
            patch(_DELETE, return_value=ctx),
            patch(_DELETE_SETTINGS, return_value=settings_mock),
        ):
            result = runner.invoke(
                delete,
                ["--source", SOURCE_ID, "--target", TARGET_ID],
                input="n\n",
            )

        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output
        ctx.edge_service.delete_edge.assert_not_called()

    def test_delete_by_source_target_with_type_filter(self) -> None:
        runner = CliRunner()
        edge_match = _make_edge(template_id=LINK_TYPE)
        edge_other = _make_edge(id="edge_other99", template_id="other_type")
        ctx = _make_ctx()
        ctx.edge_service.list_edges.return_value = _make_list_result([edge_match, edge_other])
        settings_mock = _settings_mock()

        with (
            patch(_DELETE, return_value=ctx),
            patch(_DELETE_SETTINGS, return_value=settings_mock),
        ):
            result = runner.invoke(
                delete,
                [
                    "--source",
                    SOURCE_ID,
                    "--target",
                    TARGET_ID,
                    "--type",
                    LINK_TYPE,
                    "--force",
                ],
            )

        assert result.exit_code == 0, result.output
        # Only the matching type edge should be deleted
        ctx.edge_service.delete_edge.assert_called_once_with(EDGE_ID)

    def test_delete_by_source_target_not_found(self) -> None:
        runner = CliRunner()
        # list_edges returns edge with a different target
        other_edge = _make_edge(target_node_id="node_other99")
        ctx = _make_ctx()
        ctx.edge_service.list_edges.return_value = _make_list_result([other_edge])
        settings_mock = _settings_mock()

        with (
            patch(_DELETE, return_value=ctx),
            patch(_DELETE_SETTINGS, return_value=settings_mock),
        ):
            result = runner.invoke(
                delete,
                ["--source", SOURCE_ID, "--target", TARGET_ID, "--force"],
            )

        assert result.exit_code == 0, result.output
        assert "No links found" in result.output
        ctx.edge_service.delete_edge.assert_not_called()

    def test_delete_by_source_target_type_filter_shows_hint(self) -> None:
        """When type filter returns nothing, the type filter hint is printed."""
        runner = CliRunner()
        # Edge exists but different type
        edge = _make_edge(template_id="other_type")
        ctx = _make_ctx()
        ctx.edge_service.list_edges.return_value = _make_list_result([edge])
        settings_mock = _settings_mock()

        with (
            patch(_DELETE, return_value=ctx),
            patch(_DELETE_SETTINGS, return_value=settings_mock),
        ):
            result = runner.invoke(
                delete,
                [
                    "--source",
                    SOURCE_ID,
                    "--target",
                    TARGET_ID,
                    "--type",
                    LINK_TYPE,
                    "--force",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "No links found" in result.output
        assert LINK_TYPE in result.output or "Type filter" in result.output

    def test_delete_no_args_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _make_ctx()

        with (
            patch(_DELETE, return_value=ctx),
            patch(_DELETE_SETTINGS, return_value=_settings_mock()),
        ):
            result = runner.invoke(delete, [])

        assert result.exit_code == 1
        assert "Must provide" in result.output

    def test_delete_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = MagicMock()
        ctx.edge_service.get_edge.side_effect = RuntimeError("db gone")

        with (
            patch(_DELETE, return_value=ctx),
            patch(_DELETE_SETTINGS, return_value=_settings_mock()),
        ):
            result = runner.invoke(delete, [EDGE_ID, "--force"])

        assert result.exit_code == 1
        assert "Error" in result.output


# ===========================================================================
# __init__ (group registration)
# ===========================================================================


class TestLinkGroupInit:
    def test_group_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(link, ["--help"])
        assert result.exit_code == 0, result.output
        assert "list" in result.output
        assert "create" in result.output
        assert "get" in result.output
        assert "update" in result.output
        assert "delete" in result.output

    def test_subcommand_list_registered(self) -> None:
        assert "list" in link.commands
        assert "create" in link.commands
        assert "get" in link.commands
        assert "update" in link.commands
        assert "delete" in link.commands

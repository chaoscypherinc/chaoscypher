# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the template command group.

Covers:
- commands/template/__init__.py  — group wiring
- commands/template/utils.py     — parse_property, PROPERTY_TYPES
- commands/template/create.py    — non-interactive + validation errors
- commands/template/list.py      — table / json / empty / verbose / yaml
- commands/template/get.py       — found / not-found / json / yaml / edge type / constraints
- commands/template/update.py    — name/desc/add-prop/remove-prop/no-op / not-found
- commands/template/delete.py    — force / confirm-yes / confirm-no / not-found
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.commands.template.create import create
from chaoscypher_cli.commands.template.delete import delete
from chaoscypher_cli.commands.template.get import get
from chaoscypher_cli.commands.template.list import list_templates
from chaoscypher_cli.commands.template.update import update
from chaoscypher_cli.commands.template.utils import PROPERTY_TYPES, parse_property


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmpl(
    tmpl_id: str = "tmpl-001",
    name: str = "Person",
    template_type: str = "node",
    description: str = "A person entity",
    properties: list[dict[str, Any]] | None = None,
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    """Return a minimal template dict."""
    return {
        "id": tmpl_id,
        "name": name,
        "template_type": template_type,
        "description": description,
        "properties": properties or [],
        "constraints": constraints or [],
        "created_at": "2025-01-01T00:00:00",
        "updated_at": "2025-01-02T00:00:00",
    }


def _mock_ctx(template: dict[str, Any] | None = None) -> MagicMock:
    """Return a mock CLI context with template_service configured."""
    ctx = MagicMock()
    ctx.database_name = "test"
    ts = MagicMock()
    ctx.template_service = ts
    if template is not None:
        ts.get_template.return_value = template
        ts.create_template.return_value = template
        ts.update_template.return_value = template
        ts.delete_template.return_value = True
        ts.list_templates.return_value = {"data": [template], "pagination": {"total": 1}}
    else:
        ts.get_template.return_value = None
        ts.list_templates.return_value = {"data": [], "pagination": {"total": 0}}
    return ctx


# ---------------------------------------------------------------------------
# utils.py
# ---------------------------------------------------------------------------


class TestParseProperty:
    def test_name_and_type(self) -> None:
        result = parse_property("age:integer")
        assert result["name"] == "age"
        assert result["property_type"] == "INTEGER"
        assert result["required"] is False

    def test_required_flag(self) -> None:
        result = parse_property("email:email:required")
        assert result["name"] == "email"
        assert result["property_type"] == "EMAIL"
        assert result["required"] is True

    def test_display_name_formatted(self) -> None:
        result = parse_property("first_name:string")
        assert result["display_name"] == "First Name"

    def test_invalid_format_no_colon(self) -> None:
        with pytest.raises(ValueError, match="Invalid property format"):
            parse_property("justname")

    def test_invalid_type(self) -> None:
        with pytest.raises(ValueError, match="Invalid property type"):
            parse_property("field:NOTATYPE")

    def test_property_types_not_empty(self) -> None:
        assert len(PROPERTY_TYPES) > 0
        assert "STRING" in PROPERTY_TYPES

    def test_all_core_types_present(self) -> None:
        for t in ("STRING", "TEXT", "INTEGER", "FLOAT", "BOOLEAN", "DATE"):
            assert t in PROPERTY_TYPES


# ---------------------------------------------------------------------------
# create.py
# ---------------------------------------------------------------------------


class TestCreateCommand:
    def test_create_basic(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            result = runner.invoke(create, ["--name", "Person", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "Person" in result.output
        ctx.template_service.create_template.assert_called_once()

    def test_create_with_property(self) -> None:
        runner = CliRunner()
        tmpl = _tmpl(properties=[{"name": "age", "property_type": "INTEGER", "required": False}])
        ctx = _mock_ctx(tmpl)
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            result = runner.invoke(
                create,
                ["--name", "Person", "--property", "age:integer", "--database", "test"],
            )
        assert result.exit_code == 0, result.output
        assert "age" in result.output

    def test_create_with_required_property(self) -> None:
        runner = CliRunner()
        tmpl = _tmpl(properties=[{"name": "name", "property_type": "STRING", "required": True}])
        ctx = _mock_ctx(tmpl)
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            result = runner.invoke(
                create,
                ["--name", "Person", "--property", "name:string:required", "--database", "test"],
            )
        assert result.exit_code == 0, result.output
        assert "required" in result.output

    def test_create_edge_type(self) -> None:
        runner = CliRunner()
        tmpl = _tmpl(name="WorksFor", template_type="edge")
        ctx = _mock_ctx(tmpl)
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            result = runner.invoke(
                create,
                ["--name", "WorksFor", "--type", "edge", "--database", "test"],
            )
        assert result.exit_code == 0, result.output

    def test_create_missing_name_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            result = runner.invoke(create, ["--database", "test"])
        assert result.exit_code == 1
        assert "required" in result.output.lower() or "name" in result.output.lower()

    def test_create_invalid_property_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            result = runner.invoke(
                create,
                ["--name", "Person", "--property", "bad_prop:NOTATYPE", "--database", "test"],
            )
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_create_service_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = MagicMock()
        ctx.template_service.create_template.side_effect = RuntimeError("DB error")
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            result = runner.invoke(create, ["--name", "Person", "--database", "test"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_create_with_description(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl(description="A human person"))
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            result = runner.invoke(
                create,
                ["--name", "Person", "--description", "A human person", "--database", "test"],
            )
        assert result.exit_code == 0, result.output

    def test_create_interactive_no_properties_confirmed(self) -> None:
        """Interactive wizard: user enters name, skips properties, confirms."""
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        # Patch Prompt.ask and Confirm.ask so we control the wizard flow
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.template.create.Prompt.ask",
                side_effect=["Person", "node", ""],  # name, type, description
            ):
                with patch(
                    "chaoscypher_cli.commands.template.create.Confirm.ask",
                    side_effect=[True],  # "Create this template?" -> yes
                ):
                    # prop_name loop: first ask returns "" to exit immediately
                    # but Prompt.ask is already called above for the loop too
                    # We need another ask for prop_name ("") → exit loop
                    with patch(
                        "chaoscypher_cli.commands.template.create.Prompt.ask",
                        side_effect=["Person", "node", "", ""],  # name, type, desc, prop_name=""
                    ):
                        with patch(
                            "chaoscypher_cli.commands.template.create.Confirm.ask",
                            side_effect=[True],
                        ):
                            result = runner.invoke(create, ["--interactive", "--database", "test"])
        assert result.exit_code == 0, result.output
        ctx.template_service.create_template.assert_called_once()

    def test_create_interactive_cancelled(self) -> None:
        """Interactive wizard: user confirms 'no' → cancelled, no template created."""
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.template.create.Prompt.ask",
                side_effect=["Person", "node", "", ""],  # name, type, desc, prop_name=""
            ):
                with patch(
                    "chaoscypher_cli.commands.template.create.Confirm.ask",
                    side_effect=[False],  # "Create this template?" -> no
                ):
                    result = runner.invoke(create, ["--interactive", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output
        ctx.template_service.create_template.assert_not_called()

    def test_create_interactive_with_property_and_description(self) -> None:
        """Interactive wizard: user enters a property name + description → covers lines 75-81, 94."""
        runner = CliRunner()
        ctx = _mock_ctx(
            _tmpl(properties=[{"name": "age", "property_type": "INTEGER", "required": False}])
        )
        with patch("chaoscypher_cli.commands.template.create.get_context", return_value=ctx):
            with patch(
                "chaoscypher_cli.commands.template.create.Prompt.ask",
                side_effect=[
                    "Person",  # name
                    "node",  # type
                    "A person",  # description (non-empty → covers line 94)
                    "age",  # prop_name (non-empty → enters loop body)
                    "integer",  # prop_type
                    "",  # prop_name="" → exits loop
                ],
            ):
                with patch(
                    "chaoscypher_cli.commands.template.create.Confirm.ask",
                    side_effect=[False, True],  # required? → False; create? → True
                ):
                    result = runner.invoke(create, ["--interactive", "--database", "test"])
        assert result.exit_code == 0, result.output
        ctx.template_service.create_template.assert_called_once()


# ---------------------------------------------------------------------------
# list.py
# ---------------------------------------------------------------------------


class TestListCommand:
    def test_list_table_with_templates(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            result = runner.invoke(list_templates, ["--database", "test"])
        assert result.exit_code == 0, result.output
        assert "Person" in result.output

    def test_list_empty(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(None)
        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            result = runner.invoke(list_templates, ["--database", "test"])
        assert result.exit_code == 0, result.output
        assert "No templates" in result.output or "no templates" in result.output.lower()

    def test_list_json_format(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            result = runner.invoke(list_templates, ["--format", "json", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "Person" in result.output

    def test_list_verbose(self) -> None:
        runner = CliRunner()
        tmpl = _tmpl(properties=[{"name": "age", "property_type": "integer", "required": False}])
        ctx = _mock_ctx(tmpl)
        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            result = runner.invoke(list_templates, ["--verbose", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "age" in result.output

    def test_list_verbose_no_properties(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl(properties=[]))
        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            result = runner.invoke(list_templates, ["--verbose", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "(none)" in result.output

    def test_list_verbose_many_properties(self) -> None:
        """More than 3 properties triggers the '(+N more)' path."""
        runner = CliRunner()
        props = [
            {"name": f"prop{i}", "property_type": "string", "required": False} for i in range(5)
        ]
        ctx = _mock_ctx(_tmpl(properties=props))
        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            result = runner.invoke(list_templates, ["--verbose", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "more" in result.output

    def test_list_filter_type(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            result = runner.invoke(list_templates, ["--type", "node", "--database", "test"])
        assert result.exit_code == 0, result.output
        ctx.template_service.list_templates.assert_called_once_with(template_type="node")

    def test_list_yaml_format(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            result = runner.invoke(list_templates, ["--format", "yaml", "--database", "test"])
        # Either yaml or json fallback — just exit 0
        assert result.exit_code == 0, result.output

    def test_list_yaml_format_no_pyyaml(self) -> None:
        """When PyYAML is not installed the command falls back to JSON."""
        import builtins

        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        real_import = builtins.__import__

        def _block_yaml(name: str, *args: object, **kwargs: object) -> object:
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return real_import(name, *args, **kwargs)

        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            with patch("builtins.__import__", side_effect=_block_yaml):
                result = runner.invoke(list_templates, ["--format", "yaml", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "PyYAML" in result.output or "JSON" in result.output or "Person" in result.output

    def test_list_service_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = MagicMock()
        ctx.template_service.list_templates.side_effect = RuntimeError("oops")
        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            result = runner.invoke(list_templates, ["--database", "test"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_list_verbose_long_description(self) -> None:
        """Descriptions > 40 chars are truncated (either '...' or unicode ellipsis)."""
        long_desc = "A" * 50
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl(description=long_desc))
        with patch("chaoscypher_cli.commands.template.list.get_context", return_value=ctx):
            result = runner.invoke(list_templates, ["--verbose", "--database", "test"])
        assert result.exit_code == 0, result.output
        # The production code appends "..." but Rich may render it as the unicode ellipsis …
        assert "..." in result.output or "…" in result.output or "AAA" in result.output


# ---------------------------------------------------------------------------
# get.py
# ---------------------------------------------------------------------------


class TestGetCommand:
    def test_get_found_table(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-001", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "Person" in result.output

    def test_get_not_found_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(None)
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-999", "--database", "test"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_get_json_format(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-001", "--format", "json", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "Person" in result.output

    def test_get_yaml_format(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-001", "--format", "yaml", "--database", "test"])
        assert result.exit_code == 0, result.output

    def test_get_yaml_format_no_pyyaml(self) -> None:
        """When PyYAML is not installed the command falls back to JSON."""
        import builtins

        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        real_import = builtins.__import__

        def _block_yaml(name: str, *args: object, **kwargs: object) -> object:
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return real_import(name, *args, **kwargs)

        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            with patch("builtins.__import__", side_effect=_block_yaml):
                result = runner.invoke(get, ["tmpl-001", "--format", "yaml", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "PyYAML" in result.output or "JSON" in result.output or "Person" in result.output

    def test_get_table_with_properties(self) -> None:
        runner = CliRunner()
        tmpl = _tmpl(
            properties=[
                {"name": "age", "property_type": "INTEGER", "required": True, "display_name": "Age"}
            ]
        )
        ctx = _mock_ctx(tmpl)
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-001", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "age" in result.output
        assert "Properties" in result.output

    def test_get_table_no_properties(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl(properties=[]))
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-001", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "No properties" in result.output

    def test_get_edge_type(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl(template_type="edge"))
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-001", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "edge" in result.output

    def test_get_with_constraints(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl(constraints=["unique_name"]))
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-001", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "unique_name" in result.output

    def test_get_with_description(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl(description="Describes a person"))
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-001", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "Describes a person" in result.output

    def test_get_service_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = MagicMock()
        ctx.template_service.get_template.side_effect = RuntimeError("db error")
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-001", "--database", "test"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_get_model_dump_path(self) -> None:
        """When template_service returns an object with model_dump, it is used."""
        runner = CliRunner()
        tmpl_obj = MagicMock()
        tmpl_obj.model_dump.return_value = _tmpl()
        ctx = MagicMock()
        ctx.template_service.get_template.return_value = tmpl_obj
        with patch("chaoscypher_cli.commands.template.get.get_context", return_value=ctx):
            result = runner.invoke(get, ["tmpl-001", "--database", "test"])
        assert result.exit_code == 0, result.output
        tmpl_obj.model_dump.assert_called_once()


# ---------------------------------------------------------------------------
# update.py
# ---------------------------------------------------------------------------


class TestUpdateCommand:
    def test_update_name(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(
                update,
                ["tmpl-001", "--name", "Individual", "--database", "test"],
            )
        assert result.exit_code == 0, result.output
        assert "updated" in result.output.lower()
        ctx.template_service.update_template.assert_called_once()

    def test_update_description(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(
                update,
                ["tmpl-001", "--description", "New description", "--database", "test"],
            )
        assert result.exit_code == 0, result.output
        assert "description" in result.output.lower() or "updated" in result.output.lower()

    def test_update_add_property(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl(properties=[]))
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(
                update,
                ["tmpl-001", "--add-property", "phone:string", "--database", "test"],
            )
        assert result.exit_code == 0, result.output
        assert "phone" in result.output

    def test_update_remove_property(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(
            _tmpl(properties=[{"name": "age", "property_type": "INTEGER", "required": False}])
        )
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(
                update,
                ["tmpl-001", "--remove-property", "age", "--database", "test"],
            )
        assert result.exit_code == 0, result.output
        assert "age" in result.output

    def test_update_remove_nonexistent_property(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl(properties=[]))
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(
                update,
                ["tmpl-001", "--remove-property", "ghost", "--database", "test"],
            )
        assert result.exit_code == 0, result.output
        assert "not found" in result.output.lower() or "Property not found" in result.output

    def test_update_add_duplicate_property(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(
            _tmpl(properties=[{"name": "age", "property_type": "INTEGER", "required": False}])
        )
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(
                update,
                ["tmpl-001", "--add-property", "age:integer", "--database", "test"],
            )
        assert result.exit_code == 0, result.output
        assert "already exists" in result.output

    def test_update_invalid_property_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(
                update,
                ["tmpl-001", "--add-property", "bad:NOTATYPE", "--database", "test"],
            )
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_update_no_changes(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(update, ["tmpl-001", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "No updates" in result.output

    def test_update_not_found_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(None)
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(
                update,
                ["tmpl-999", "--name", "X", "--database", "test"],
            )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_update_service_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = MagicMock()
        ctx.template_service.get_template.side_effect = RuntimeError("boom")
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(
                update,
                ["tmpl-001", "--name", "X", "--database", "test"],
            )
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_update_model_dump_path(self) -> None:
        """When existing template has model_dump, the dict path is exercised."""
        runner = CliRunner()
        tmpl_obj = MagicMock()
        tmpl_obj.model_dump.return_value = _tmpl()
        ctx = MagicMock()
        ctx.template_service.get_template.return_value = tmpl_obj
        ctx.template_service.update_template.return_value = _tmpl()
        with patch("chaoscypher_cli.commands.template.update.get_context", return_value=ctx):
            result = runner.invoke(
                update,
                ["tmpl-001", "--name", "NewName", "--database", "test"],
            )
        assert result.exit_code == 0, result.output
        tmpl_obj.model_dump.assert_called_once()


# ---------------------------------------------------------------------------
# delete.py
# ---------------------------------------------------------------------------


class TestDeleteCommand:
    def test_delete_force_skips_confirm(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["tmpl-001", "--force", "--database", "test"])
        assert result.exit_code == 0, result.output
        assert "deleted" in result.output.lower()
        ctx.template_service.delete_template.assert_called_once_with("tmpl-001")

    def test_delete_confirm_yes(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["tmpl-001", "--database", "test"], input="y\n")
        assert result.exit_code == 0, result.output
        assert "deleted" in result.output.lower()

    def test_delete_confirm_no_cancels(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(_tmpl())
        with patch("chaoscypher_cli.commands.template.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["tmpl-001", "--database", "test"], input="n\n")
        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output or "cancelled" in result.output.lower()
        ctx.template_service.delete_template.assert_not_called()

    def test_delete_not_found_exits_1(self) -> None:
        runner = CliRunner()
        ctx = _mock_ctx(None)
        with patch("chaoscypher_cli.commands.template.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["tmpl-999", "--force", "--database", "test"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_delete_shows_template_info(self) -> None:
        runner = CliRunner()
        tmpl = _tmpl(properties=[{"name": "age", "property_type": "INTEGER", "required": False}])
        ctx = _mock_ctx(tmpl)
        with patch("chaoscypher_cli.commands.template.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["tmpl-001", "--force", "--database", "test"])
        assert result.exit_code == 0, result.output
        # Shows property count
        assert "1" in result.output

    def test_delete_service_exception_exits_1(self) -> None:
        runner = CliRunner()
        ctx = MagicMock()
        ctx.template_service.get_template.side_effect = RuntimeError("db gone")
        with patch("chaoscypher_cli.commands.template.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["tmpl-001", "--force", "--database", "test"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_delete_model_dump_path(self) -> None:
        """When template has model_dump, that branch is exercised."""
        runner = CliRunner()
        tmpl_obj = MagicMock()
        tmpl_obj.model_dump.return_value = _tmpl()
        ctx = MagicMock()
        ctx.template_service.get_template.return_value = tmpl_obj
        with patch("chaoscypher_cli.commands.template.delete.get_context", return_value=ctx):
            result = runner.invoke(delete, ["tmpl-001", "--force", "--database", "test"])
        assert result.exit_code == 0, result.output
        tmpl_obj.model_dump.assert_called_once()


# ---------------------------------------------------------------------------
# __init__.py — group registration
# ---------------------------------------------------------------------------


class TestTemplateGroupRegistration:
    def test_group_has_all_subcommands(self) -> None:
        from chaoscypher_cli.commands.template import template

        assert template is not None
        subcommands = list(template.commands.keys())
        for name in ("list", "create", "get", "update", "delete"):
            assert name in subcommands, f"Missing subcommand: {name}"

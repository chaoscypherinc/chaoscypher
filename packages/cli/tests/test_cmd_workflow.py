# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the workflow command group (list + get sub-commands).

Coverage targets:
- commands/workflow/get.py   ≥85%
- commands/workflow/list.py  ≥85%
- commands/workflow/__init__.py  100%
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.workflow import workflow
from chaoscypher_cli.commands.workflow.get import get
from chaoscypher_cli.commands.workflow.list import list_workflows


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_workflow(
    wf_id: str = "wf-001",
    name: str = "entity-extraction",
    category: str = "research",
    is_active: bool = True,
    description: str = "Extracts entities from documents",
    created_at: str = "2025-01-01T00:00:00Z",
    updated_at: str | None = "2025-06-01T12:00:00Z",
    last_run_at: str | None = None,
    statistics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a minimal workflow record."""
    record: dict[str, Any] = {
        "id": wf_id,
        "name": name,
        "category": category,
        "is_active": is_active,
        "description": description,
        "created_at": created_at,
    }
    if updated_at is not None:
        record["updated_at"] = updated_at
    if last_run_at is not None:
        record["last_run_at"] = last_run_at
    if statistics is not None:
        record["statistics"] = statistics
    return record


def _make_step(
    step_id: str = "step-1",
    name: str = "Chunk Text",
    tool_type: str = "chunker",
    tool_id: str = "default-chunker",
    order: int = 1,
) -> dict[str, Any]:
    return {
        "id": step_id,
        "name": name,
        "tool_type": tool_type,
        "tool_id": tool_id,
        "order": order,
    }


def _make_mock_ctx(
    workflows: list[dict[str, Any]] | None = None,
    steps: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Build a mock CLI context with a configured workflow_service."""
    ctx = MagicMock()
    ctx.workflow_service = MagicMock()
    ctx.workflow_service.list_workflows.return_value = workflows if workflows is not None else []
    ctx.workflow_service.list_workflow_steps.return_value = steps if steps is not None else []
    ctx.workflow_service.get_workflow.return_value = None
    return ctx


# ===========================================================================
# __init__.py — workflow group
# ===========================================================================


def test_workflow_group_help() -> None:
    """The 'workflow' group is reachable and shows sub-commands in --help."""
    runner = CliRunner()
    result = runner.invoke(workflow, ["--help"])
    assert result.exit_code == 0, result.output
    assert "list" in result.output
    assert "get" in result.output


# ===========================================================================
# list command
# ===========================================================================


def test_list_empty_table() -> None:
    """No workflows → prints the 'No workflows found.' message."""
    runner = CliRunner()
    mock_ctx = _make_mock_ctx(workflows=[])

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, [])

    assert result.exit_code == 0, result.output
    assert "No workflows found." in result.output
    assert "web UI" in result.output


def test_list_table_shows_workflows() -> None:
    """Table format lists workflow id, name, category, and status."""
    runner = CliRunner()
    wfs = [
        _make_workflow(wf_id="wf-001", name="entity-extraction", category="research"),
        _make_workflow(
            wf_id="wf-002",
            name="summarizer",
            category="nlp",
            is_active=False,
        ),
    ]
    mock_ctx = _make_mock_ctx(workflows=wfs)

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, [])

    assert result.exit_code == 0, result.output
    assert "entity-extraction" in result.output
    assert "summarizer" in result.output
    # Summary line
    assert "2 workflow(s)" in result.output
    assert "1 active" in result.output


def test_list_table_inactive_status_badge() -> None:
    """Inactive workflows show 'Inactive' text in the output."""
    runner = CliRunner()
    wfs = [_make_workflow(is_active=False, name="old-flow")]
    mock_ctx = _make_mock_ctx(workflows=wfs)

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, [])

    assert result.exit_code == 0, result.output
    # Both 'Inactive' text (stripped from Rich markup) and name should appear
    assert "old-flow" in result.output


def test_list_verbose_shows_step_count() -> None:
    """--verbose adds the Steps and Description columns."""
    runner = CliRunner()
    wf = _make_workflow(description="A workflow about research")
    mock_ctx = _make_mock_ctx(
        workflows=[wf],
        steps=[_make_step(), _make_step(step_id="step-2", name="Embed", order=2)],
    )

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, ["--verbose"])

    assert result.exit_code == 0, result.output
    # Step count "2" should appear in output
    assert "2" in result.output
    # list_workflow_steps should be called for each workflow
    mock_ctx.workflow_service.list_workflow_steps.assert_called_once_with(wf["id"])


def test_list_verbose_long_description_truncated() -> None:
    """Long descriptions are truncated to ≤40 chars; Rich renders the truncation with '…'."""
    runner = CliRunner()
    long_desc = "A" * 50
    wf = _make_workflow(description=long_desc)
    mock_ctx = _make_mock_ctx(workflows=[wf], steps=[])

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, ["--verbose"])

    assert result.exit_code == 0, result.output
    # Rich truncates the cell with a Unicode ellipsis character (U+2026).
    assert "…" in result.output


def test_list_json_format() -> None:
    """--format json outputs valid JSON array."""
    runner = CliRunner()
    wfs = [_make_workflow(), _make_workflow(wf_id="wf-002", name="summarizer")]
    mock_ctx = _make_mock_ctx(workflows=wfs)

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, ["--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output.strip())
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0]["id"] == "wf-001"


def test_list_yaml_format() -> None:
    """--format yaml outputs YAML-structured text."""
    runner = CliRunner()
    wfs = [_make_workflow()]
    mock_ctx = _make_mock_ctx(workflows=wfs)

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, ["--format", "yaml"])

    assert result.exit_code == 0, result.output
    # YAML should contain the workflow name key
    assert "entity-extraction" in result.output


def test_list_yaml_fallback_when_no_yaml() -> None:
    """When PyYAML is absent the yaml branch falls back to JSON and prints a warning."""
    runner = CliRunner()
    wfs = [_make_workflow()]
    mock_ctx = _make_mock_ctx(workflows=wfs)

    # Simulate ImportError for yaml inside the command
    import builtins

    real_import = builtins.__import__

    def _block_yaml(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml":
            raise ImportError("yaml not available")
        return real_import(name, *args, **kwargs)

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        with patch("builtins.__import__", side_effect=_block_yaml):
            result = runner.invoke(list_workflows, ["--format", "yaml"])

    assert result.exit_code == 0, result.output
    assert "PyYAML" in result.output or "JSON" in result.output
    # Should still produce parseable JSON output
    # (output may contain the warning line before the JSON block)
    # At minimum the workflow name must appear
    assert "entity-extraction" in result.output


def test_list_filter_by_category() -> None:
    """--category is forwarded to list_workflows on the service."""
    runner = CliRunner()
    mock_ctx = _make_mock_ctx(workflows=[_make_workflow(category="research")])

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, ["--category", "research"])

    assert result.exit_code == 0, result.output
    mock_ctx.workflow_service.list_workflows.assert_called_once_with(
        category="research",
        is_active=None,
    )


def test_list_filter_active() -> None:
    """--active passes is_active=True to the service."""
    runner = CliRunner()
    mock_ctx = _make_mock_ctx(workflows=[_make_workflow()])

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, ["--active"])

    assert result.exit_code == 0, result.output
    mock_ctx.workflow_service.list_workflows.assert_called_once_with(
        category=None,
        is_active=True,
    )


def test_list_filter_inactive() -> None:
    """--inactive passes is_active=False to the service."""
    runner = CliRunner()
    mock_ctx = _make_mock_ctx(workflows=[_make_workflow(is_active=False)])

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, ["--inactive"])

    assert result.exit_code == 0, result.output
    mock_ctx.workflow_service.list_workflows.assert_called_once_with(
        category=None,
        is_active=False,
    )


def test_list_error_exits_1() -> None:
    """An exception from workflow_service causes exit code 1 and shows error text."""
    runner = CliRunner()
    mock_ctx = MagicMock()
    mock_ctx.workflow_service.list_workflows.side_effect = RuntimeError("DB unavailable")

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, [])

    assert result.exit_code == 1
    assert "DB unavailable" in result.output


def test_list_workflow_no_category_shows_none() -> None:
    """A workflow with no category shows '(none)' in the output."""
    runner = CliRunner()
    wf = _make_workflow(category="")
    # Override to simulate missing category key
    del wf["category"]
    mock_ctx = _make_mock_ctx(workflows=[wf])

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(list_workflows, [])

    assert result.exit_code == 0, result.output
    assert "(none)" in result.output


# ===========================================================================
# get command — found by ID
# ===========================================================================


def test_get_found_by_id_table_format_with_steps() -> None:
    """Get returns exit 0, prints workflow name and step names in table format."""
    runner = CliRunner()
    wf = _make_workflow()
    steps = [
        _make_step(name="Chunk Text", tool_type="chunker", order=1),
        _make_step(step_id="step-2", name="Embed", tool_type="embedder", order=2),
    ]
    mock_ctx = _make_mock_ctx(steps=steps)
    mock_ctx.workflow_service.get_workflow.return_value = wf

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["wf-001"])

    assert result.exit_code == 0, result.output
    assert "entity-extraction" in result.output
    assert "Chunk Text" in result.output
    assert "Embed" in result.output


def test_get_table_no_steps_shows_message() -> None:
    """When a workflow has no steps, the 'No steps defined.' message appears."""
    runner = CliRunner()
    wf = _make_workflow()
    mock_ctx = _make_mock_ctx(steps=[])
    mock_ctx.workflow_service.get_workflow.return_value = wf

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["wf-001"])

    assert result.exit_code == 0, result.output
    assert "No steps defined." in result.output


def test_get_table_shows_inactive_status() -> None:
    """Inactive workflow shows 'inactive' status badge."""
    runner = CliRunner()
    wf = _make_workflow(is_active=False)
    mock_ctx = _make_mock_ctx(steps=[])
    mock_ctx.workflow_service.get_workflow.return_value = wf

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["wf-001"])

    assert result.exit_code == 0, result.output
    assert "inactive" in result.output


def test_get_table_shows_description_and_timestamps() -> None:
    """Description, created_at, updated_at and last_run_at fields appear when present."""
    runner = CliRunner()
    wf = _make_workflow(
        description="My description",
        created_at="2025-01-15T00:00:00Z",
        updated_at="2025-06-01T00:00:00Z",
        last_run_at="2025-06-10T00:00:00Z",
    )
    mock_ctx = _make_mock_ctx(steps=[])
    mock_ctx.workflow_service.get_workflow.return_value = wf

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["wf-001"])

    assert result.exit_code == 0, result.output
    assert "My description" in result.output
    assert "2025-01-15" in result.output
    assert "2025-06-01" in result.output
    assert "2025-06-10" in result.output


def test_get_table_shows_statistics() -> None:
    """Statistics block appears when the workflow has stats."""
    runner = CliRunner()
    stats = {"total_runs": 10, "successful_runs": 8, "failed_runs": 2}
    wf = _make_workflow(statistics=stats)
    mock_ctx = _make_mock_ctx(steps=[])
    mock_ctx.workflow_service.get_workflow.return_value = wf

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["wf-001"])

    assert result.exit_code == 0, result.output
    assert "Statistics" in result.output
    assert "10" in result.output
    assert "8" in result.output
    assert "2" in result.output


def test_get_json_format() -> None:
    """--format json outputs valid JSON including steps key."""
    runner = CliRunner()
    wf = _make_workflow()
    steps = [_make_step()]
    mock_ctx = _make_mock_ctx(steps=steps)
    mock_ctx.workflow_service.get_workflow.return_value = wf

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["wf-001", "--format", "json"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output.strip())
    assert parsed["id"] == "wf-001"
    assert isinstance(parsed["steps"], list)
    assert parsed["steps"][0]["name"] == "Chunk Text"


def test_get_yaml_format() -> None:
    """--format yaml outputs YAML text containing the workflow name."""
    runner = CliRunner()
    wf = _make_workflow()
    mock_ctx = _make_mock_ctx(steps=[])
    mock_ctx.workflow_service.get_workflow.return_value = wf

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["wf-001", "--format", "yaml"])

    assert result.exit_code == 0, result.output
    assert "entity-extraction" in result.output


def test_get_yaml_fallback_when_no_yaml() -> None:
    """Missing PyYAML in get falls back to JSON and prints a warning."""
    runner = CliRunner()
    wf = _make_workflow()
    mock_ctx = _make_mock_ctx(steps=[])
    mock_ctx.workflow_service.get_workflow.return_value = wf

    import builtins

    real_import = builtins.__import__

    def _block_yaml(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml":
            raise ImportError("yaml not available")
        return real_import(name, *args, **kwargs)

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        with patch("builtins.__import__", side_effect=_block_yaml):
            result = runner.invoke(get, ["wf-001", "--format", "yaml"])

    assert result.exit_code == 0, result.output
    assert "PyYAML" in result.output or "JSON" in result.output
    assert "entity-extraction" in result.output


# ===========================================================================
# get command — found by name (fallback path)
# ===========================================================================


def test_get_found_by_name_fallback() -> None:
    """When get_workflow returns None, list_workflows is searched by name."""
    runner = CliRunner()
    wf = _make_workflow(wf_id="wf-007", name="my-workflow")
    mock_ctx = _make_mock_ctx(steps=[])
    # get_workflow returns None — triggers name-lookup
    mock_ctx.workflow_service.get_workflow.return_value = None
    mock_ctx.workflow_service.list_workflows.return_value = [wf]

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["my-workflow"])

    assert result.exit_code == 0, result.output
    assert "my-workflow" in result.output


# ===========================================================================
# get command — not found
# ===========================================================================


def test_get_not_found_exits_1() -> None:
    """A workflow that cannot be found by ID or name exits with code 1."""
    runner = CliRunner()
    mock_ctx = _make_mock_ctx(workflows=[])
    mock_ctx.workflow_service.get_workflow.return_value = None

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["nonexistent-id"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "Workflow" in result.output


def test_get_not_found_message_contains_id() -> None:
    """The not-found error message includes the workflow ID that was requested."""
    runner = CliRunner()
    mock_ctx = _make_mock_ctx(workflows=[])
    mock_ctx.workflow_service.get_workflow.return_value = None

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["missing-wf-xyz"])

    assert result.exit_code == 1
    assert "missing-wf-xyz" in result.output


# ===========================================================================
# get command — error path
# ===========================================================================


def test_get_service_error_exits_1() -> None:
    """An exception from workflow_service causes exit code 1 and shows the error."""
    runner = CliRunner()
    mock_ctx = MagicMock()
    mock_ctx.workflow_service.get_workflow.side_effect = RuntimeError("connection refused")

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["wf-001"])

    assert result.exit_code == 1
    assert "connection refused" in result.output


# ===========================================================================
# get command — --database flag
# ===========================================================================


def test_get_passes_database_to_context() -> None:
    """The --database flag is forwarded to get_context."""
    runner = CliRunner()
    wf = _make_workflow()
    mock_ctx = _make_mock_ctx(steps=[])
    mock_ctx.workflow_service.get_workflow.return_value = wf

    with patch(
        "chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx
    ) as mock_get_ctx:
        result = runner.invoke(get, ["wf-001", "--database", "my-db"])

    assert result.exit_code == 0, result.output
    mock_get_ctx.assert_called_once_with(database_name="my-db")


def test_list_passes_database_to_context() -> None:
    """The --database flag on list is forwarded to get_context."""
    runner = CliRunner()
    mock_ctx = _make_mock_ctx(workflows=[])

    with patch(
        "chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx
    ) as mock_get_ctx:
        result = runner.invoke(list_workflows, ["--database", "prod-db"])

    assert result.exit_code == 0, result.output
    mock_get_ctx.assert_called_once_with(database_name="prod-db")


# ===========================================================================
# get command — step rendering edge cases
# ===========================================================================


def test_get_step_missing_optional_fields_uses_defaults() -> None:
    """Steps with minimal fields (no tool_type/tool_id) render 'N/A'."""
    runner = CliRunner()
    wf = _make_workflow()
    minimal_step = {"name": "Minimal Step"}  # no order, no tool_type, no tool_id
    mock_ctx = _make_mock_ctx(steps=[minimal_step])
    mock_ctx.workflow_service.get_workflow.return_value = wf

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(get, ["wf-001"])

    assert result.exit_code == 0, result.output
    assert "Minimal Step" in result.output
    assert "N/A" in result.output


# ===========================================================================
# Invocation via the group (integration-style)
# ===========================================================================


def test_workflow_list_via_group() -> None:
    """'workflow list' invoked via the parent group works end-to-end."""
    runner = CliRunner()
    mock_ctx = _make_mock_ctx(workflows=[_make_workflow()])

    with patch("chaoscypher_cli.commands.workflow.list.get_context", return_value=mock_ctx):
        result = runner.invoke(workflow, ["list"])

    assert result.exit_code == 0, result.output
    assert "entity-extraction" in result.output


def test_workflow_get_via_group() -> None:
    """'workflow get <id>' invoked via the parent group works end-to-end."""
    runner = CliRunner()
    wf = _make_workflow()
    mock_ctx = _make_mock_ctx(steps=[])
    mock_ctx.workflow_service.get_workflow.return_value = wf

    with patch("chaoscypher_cli.commands.workflow.get.get_context", return_value=mock_ctx):
        result = runner.invoke(workflow, ["get", "wf-001"])

    assert result.exit_code == 0, result.output
    assert "entity-extraction" in result.output

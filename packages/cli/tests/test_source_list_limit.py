# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""source list honors --limit and surfaces truncation.

The command called ``list_files`` with the adapter default (limit=100)
and printed ``Total: {len(files)}`` — a database with 250 sources
silently read as "Total: 100 file(s)". Now: ``--limit`` is forwarded to
the adapter, and when more rows exist than were fetched the table
footer says "Showing first N of M" using the real total from the
adapter count helpers.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.source.list import list_files


def _files(n: int) -> list[dict[str, Any]]:
    return [
        {"id": f"if_{i:011d}", "filename": f"f{i}.pdf", "status": "indexed", "file_size": 10}
        for i in range(n)
    ]


def _ctx(*, files: list[dict[str, Any]], total: int) -> MagicMock:
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.list_files.return_value = files
    ctx.storage_adapter.count_sources.return_value = total
    ctx.storage_adapter.count_sources_by_statuses.return_value = total
    return ctx


def test_limit_option_forwarded_to_adapter() -> None:
    """--limit N reaches list_files as limit=N."""
    runner = CliRunner()
    ctx = _ctx(files=_files(5), total=5)

    with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
        result = runner.invoke(list_files, ["--limit", "5"])

    assert result.exit_code == 0, result.output
    _, kwargs = ctx.storage_adapter.list_files.call_args
    assert kwargs.get("limit") == 5


def test_truncation_hint_shows_real_total() -> None:
    """More rows than fetched → 'Showing first N of M' with the adapter total."""
    runner = CliRunner()
    ctx = _ctx(files=_files(100), total=250)

    with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
        result = runner.invoke(list_files, [])

    assert result.exit_code == 0, result.output
    assert "Showing first 100 of 250" in result.output
    assert "--limit" in result.output, "hint should tell the user how to see more"


def test_truncation_hint_uses_status_scoped_count() -> None:
    """With a status filter, M comes from the status-scoped count."""
    runner = CliRunner()
    ctx = _ctx(files=_files(100), total=180)

    with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
        result = runner.invoke(list_files, ["--status", "indexed"])

    assert result.exit_code == 0, result.output
    assert "Showing first 100 of 180" in result.output
    _, kwargs = ctx.storage_adapter.count_sources_by_statuses.call_args
    assert kwargs.get("statuses") == ["indexed"]


def test_no_hint_when_everything_fits() -> None:
    """Fewer rows than the limit → plain Total footer, no truncation hint."""
    runner = CliRunner()
    ctx = _ctx(files=_files(3), total=3)

    with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
        result = runner.invoke(list_files, [])

    assert result.exit_code == 0, result.output
    assert "Total: 3 file(s)" in result.output
    assert "Showing first" not in result.output


def test_default_keeps_adapter_limit() -> None:
    """No --limit → the adapter's own default applies (no limit kwarg)."""
    runner = CliRunner()
    ctx = _ctx(files=_files(2), total=2)

    with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
        result = runner.invoke(list_files, [])

    assert result.exit_code == 0, result.output
    _, kwargs = ctx.storage_adapter.list_files.call_args
    assert "limit" not in kwargs, "default run must keep the adapter's default limit"

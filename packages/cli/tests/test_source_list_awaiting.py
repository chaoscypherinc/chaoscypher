# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""source list --awaiting filters to parked sources; status color is defined."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.source.list import list_files
from chaoscypher_cli.utils.display import get_status_color


def _files() -> list[dict[str, Any]]:
    return [
        {"id": "if_a1234567890ab"[:15], "filename": "a.pdf", "status": "awaiting_confirmation"},
        {"id": "if_b1234567890ab"[:15], "filename": "b.pdf", "status": "committed"},
        {"id": "if_c1234567890ab"[:15], "filename": "c.pdf", "status": "indexed"},
    ]


def test_awaiting_status_has_distinct_color() -> None:
    color = get_status_color("awaiting_confirmation")
    assert color != "dim"  # not the unknown-status fallback


def test_list_awaiting_filters_to_parked_only() -> None:
    runner = CliRunner()
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.list_files.return_value = [
        f for f in _files() if f["status"] == "awaiting_confirmation"
    ]

    with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
        result = runner.invoke(list_files, ["--awaiting", "--format", "json"])

    assert result.exit_code == 0
    assert "awaiting_confirmation" in result.output
    assert "committed" not in result.output
    # The status filter is passed through to the adapter.
    ctx.storage_adapter.list_files.assert_called_once_with(
        database_name="default", status="awaiting_confirmation"
    )


def test_list_has_awaiting_flag() -> None:
    params = {p.name for p in list_files.params}
    assert "awaiting" in params

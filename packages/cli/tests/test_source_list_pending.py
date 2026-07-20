# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""source list --pending excludes committed AND errored sources."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from chaoscypher_cli.commands.source.list import list_files


def _mixed_files() -> list[dict[str, Any]]:
    return [
        {"id": "if_pending00001", "filename": "p.pdf", "status": "pending"},
        {"id": "if_indexed00001", "filename": "i.pdf", "status": "indexed"},
        {"id": "if_committed001", "filename": "c.pdf", "status": "committed"},
        {"id": "if_errored00001", "filename": "e.pdf", "status": "error"},
    ]


def test_pending_excludes_committed_and_errored() -> None:
    """--pending drops committed and errored sources, keeps in-flight ones.

    Regression: the exclusion set used the literal ``"failed"``, which is not
    a real ``SourceStatus`` value (the errored status is ``error``), so
    errored sources leaked into --pending despite the help text. The filter
    now uses ``SourceStatus.ERROR``.
    """
    runner = CliRunner()
    ctx = MagicMock()
    ctx.database_name = "default"
    ctx.storage_adapter.list_files.return_value = _mixed_files()

    with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
        result = runner.invoke(list_files, ["--pending", "--format", "json"])

    assert result.exit_code == 0
    # Kept: not-yet-committed, non-errored sources.
    assert "p.pdf" in result.output
    assert "i.pdf" in result.output
    # Dropped: committed and errored.
    assert "c.pdf" not in result.output
    assert "e.pdf" not in result.output

# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``chaoscypher mcp --profile``: the served tool profile, the way ``--mode`` sets the mode."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner


def test_profile_reaches_serve_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    import chaoscypher_cli.mcp.command as cmd

    serve = MagicMock()
    monkeypatch.setattr(cmd, "serve_stdio", serve)
    result = CliRunner().invoke(cmd.mcp, ["--profile", "memory"])

    assert result.exit_code == 0, result.output
    assert serve.call_args.kwargs["profile"] == "memory"


def test_no_profile_flag_leaves_the_configured_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import chaoscypher_cli.mcp.command as cmd

    serve = MagicMock()
    monkeypatch.setattr(cmd, "serve_stdio", serve)
    assert CliRunner().invoke(cmd.mcp, []).exit_code == 0
    assert serve.call_args.kwargs["profile"] is None


def test_an_unknown_profile_is_a_choice_error() -> None:
    import chaoscypher_cli.mcp.command as cmd

    result = CliRunner().invoke(cmd.mcp, ["--profile", "notes"])
    assert result.exit_code == 2
    assert "profile" in result.output and "notes" in result.output

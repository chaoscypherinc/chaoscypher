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


def test_serve_stdio_closes_the_memory_linker(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the client disconnects, the links ``remember`` left running give their claims back."""
    import sys
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager
    from pathlib import Path
    from types import SimpleNamespace
    from typing import Any

    import chaoscypher_cli.mcp.command as cmd
    from chaoscypher_core.mcp.memory_link import MemoryLinker

    monkeypatch.setattr("chaoscypher_core.utils.logging.configure_logging", lambda **_k: None)
    monkeypatch.setitem(sys.modules, "langchain_text_splitters", SimpleNamespace())
    monkeypatch.setattr(cmd, "get_database_name", lambda _o: "journal")
    monkeypatch.setattr(
        "chaoscypher_core.database.engine.get_db_path", lambda _n: Path("/data/app.db")
    )
    monkeypatch.setattr(
        "chaoscypher_core.database.migrations.startup.run_startup_migrations", MagicMock()
    )
    monkeypatch.setattr(
        "chaoscypher_core.database.migrations.state.get_upgrade_state",
        lambda _p: SimpleNamespace(ready=True, blocked_on=[], message=""),
    )
    ctx = SimpleNamespace(
        _engine=object(),
        database_name="journal",
        settings=SimpleNamespace(
            mcp=SimpleNamespace(mode="write", profile="memory", auto_extract=False)
        ),
    )
    monkeypatch.setattr(cmd, "get_context", MagicMock(return_value=ctx))

    events: list[str] = []
    built: dict[str, Any] = {}

    class _Server:
        async def run(self, *_args: Any) -> None:
            events.append("run")

        def create_initialization_options(self) -> None:
            return None

    def create(engine: Any, **kwargs: Any) -> _Server:
        built.update(kwargs)
        return _Server()

    monkeypatch.setattr("chaoscypher_core.mcp.server.create_mcp_server", create)

    @asynccontextmanager
    async def stdio(**_kw: Any) -> AsyncIterator[tuple[None, None]]:
        yield None, None

    monkeypatch.setattr("mcp.server.stdio.stdio_server", stdio)

    async def close(self: MemoryLinker) -> None:
        events.append("close")
        built["closed"] = self

    monkeypatch.setattr(MemoryLinker, "close", close)

    cmd.serve_stdio(mode="write")

    assert events == ["run", "close"]
    assert isinstance(built["linker"], MemoryLinker)
    assert built["closed"] is built["linker"]

# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``chaoscypher mcp`` must reach maintenance mode, not exit(2), when blocked."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner


def test_mcp_is_an_upgrade_safe_subcommand() -> None:
    # The group-level _upgrade_guard exits(2) for non-safe subcommands when the
    # DB is blocked. `mcp` must bypass it so its body can start maintenance mode
    # instead of dying before the stdio handshake (the -32000 this PR fixes).
    from chaoscypher_cli.__main__ import _UPGRADE_SAFE_SUBCOMMANDS

    assert "mcp" in _UPGRADE_SAFE_SUBCOMMANDS


def _close_coro(coro: Any) -> None:
    """Stand-in for asyncio.run: close the coroutine without serving stdio."""
    coro.close()


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ready: bool,
    heal: MagicMock | None = None,
    settings_mode: str = "write",
) -> dict[str, MagicMock]:
    """Patch the command's dependencies.

    ``get_context`` / ``get_database_name`` are imported into the command
    module at top-level, so they're patched THERE (not at chaoscypher_cli.context).
    The lazily-imported helpers are patched at their source modules.

    ``settings_mode`` pins the configured ``mcp.mode`` the blocked branch reads.
    It defaults to ``"write"`` — NOT the production default — so the tests that
    exercise routing rather than the read-only guard keep reaching the server;
    the guard itself is pinned explicitly by the ``mcp.mode: read`` tests below.
    """
    import chaoscypher_cli.mcp.command as cmd

    monkeypatch.setattr("chaoscypher_core.utils.logging.configure_logging", lambda **_k: None)
    monkeypatch.setattr(cmd.asyncio, "run", _close_coro)
    monkeypatch.setitem(sys.modules, "langchain_text_splitters", SimpleNamespace())

    monkeypatch.setattr(cmd, "get_database_name", lambda _o: "warpeace")
    monkeypatch.setattr(
        "chaoscypher_core.database.engine.get_db_path", lambda _n: Path("/data/app.db")
    )
    heal_mock = heal if heal is not None else MagicMock()
    monkeypatch.setattr(
        "chaoscypher_core.database.migrations.startup.run_startup_migrations", heal_mock
    )
    monkeypatch.setattr(
        "chaoscypher_core.database.migrations.state.get_upgrade_state",
        lambda _p: SimpleNamespace(ready=ready, blocked_on=["0042"], message="blocked"),
    )

    normal = MagicMock(return_value="normal-server")
    maint = MagicMock(return_value="maint-server")
    monkeypatch.setattr("chaoscypher_core.mcp.server.create_mcp_server", normal)
    monkeypatch.setattr("chaoscypher_core.mcp.maintenance.create_maintenance_mcp_server", maint)

    fake_ctx = SimpleNamespace(
        _engine=object(),
        database_name="warpeace",
        settings=SimpleNamespace(mcp=SimpleNamespace(mode="read", auto_extract=False)),
    )
    get_ctx = MagicMock(return_value=fake_ctx)
    monkeypatch.setattr(cmd, "get_context", get_ctx)

    # The blocked branch builds no context, so it reads the configured mode
    # straight from the canonical getter.
    get_settings = MagicMock(return_value=SimpleNamespace(mcp=SimpleNamespace(mode=settings_mode)))
    monkeypatch.setattr("chaoscypher_core.app_config.get_settings", get_settings)
    return {
        "heal": heal_mock,
        "normal": normal,
        "maint": maint,
        "get_ctx": get_ctx,
        "get_settings": get_settings,
    }


def test_mcp_ready_builds_normal_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from chaoscypher_cli.mcp.command import mcp

    spies = _patch_common(monkeypatch, ready=True)
    result = CliRunner().invoke(mcp, ["--database", "warpeace"])

    assert result.exit_code == 0, result.output
    spies["heal"].assert_called_once()
    spies["normal"].assert_called_once()
    spies["maint"].assert_not_called()


def test_mcp_blocked_builds_maintenance_server(monkeypatch: pytest.MonkeyPatch) -> None:
    from chaoscypher_cli.mcp.command import mcp

    spies = _patch_common(monkeypatch, ready=False, settings_mode="write")
    result = CliRunner().invoke(mcp, ["--database", "warpeace"])

    assert result.exit_code == 0, result.output
    spies["heal"].assert_called_once()
    spies["maint"].assert_called_once_with("warpeace")
    spies["normal"].assert_not_called()
    # Blocked path must NOT build the heavy Engine.
    spies["get_ctx"].assert_not_called()


def test_mcp_blocked_honors_configured_read_mode_without_a_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured ``mcp.mode: read`` must refuse, with no ``--mode`` typed.

    Regression pin. The guard used to test the Click flag, which defaults to
    None, so read-only was honoured ONLY when the operator typed it. Since
    ``MCPSettings.mode`` itself defaults to ``"read"``, the documented Claude
    Desktop invocation — ``{"command": "chaoscypher", "args": ["mcp"]}``, no
    flag — sailed past the guard and handed an external MCP client the
    destructive ``apply_upgrade`` tool on a mount configured read-only.
    """
    from chaoscypher_cli.mcp.command import mcp

    spies = _patch_common(monkeypatch, ready=False, settings_mode="read")
    result = CliRunner().invoke(mcp, ["--database", "warpeace"])

    assert result.exit_code != 0
    assert "maintenance mode" in result.output
    assert "mcp.mode: read" in result.output
    spies["maint"].assert_not_called()
    spies["normal"].assert_not_called()


def test_mcp_blocked_write_flag_overrides_configured_read_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit ``--mode write`` still beats a configured ``read``.

    The flag is the operator's in-the-moment intent and remains the escape
    hatch the refusal message points at.
    """
    from chaoscypher_cli.mcp.command import mcp

    spies = _patch_common(monkeypatch, ready=False, settings_mode="read")
    result = CliRunner().invoke(mcp, ["--database", "warpeace", "--mode", "write"])

    assert result.exit_code == 0, result.output
    spies["maint"].assert_called_once_with("warpeace")


def test_mcp_blocked_with_read_mode_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--mode read must not be silently dropped by the maintenance branch.

    The maintenance dispatcher exposes destructive repair (apply_upgrade)
    unconditionally, so a read-only request cannot be honored — the command
    refuses with an actionable error instead of starting a writable server.
    """
    from chaoscypher_cli.mcp.command import mcp

    spies = _patch_common(monkeypatch, ready=False)
    result = CliRunner().invoke(mcp, ["--database", "warpeace", "--mode", "read"])

    assert result.exit_code != 0
    assert "maintenance mode" in result.output
    assert "--mode read" in result.output
    spies["maint"].assert_not_called()
    spies["normal"].assert_not_called()


def test_mcp_blocked_with_write_mode_still_starts_maintenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--mode write is compatible with maintenance repair; server still starts."""
    from chaoscypher_cli.mcp.command import mcp

    spies = _patch_common(monkeypatch, ready=False)
    result = CliRunner().invoke(mcp, ["--database", "warpeace", "--mode", "write"])

    assert result.exit_code == 0, result.output
    spies["maint"].assert_called_once_with("warpeace")


def test_mcp_heal_error_still_routes_to_maintenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaoscypher_cli.mcp.command import mcp

    boom = MagicMock(side_effect=RuntimeError("apply blew up mid-migration"))
    spies = _patch_common(monkeypatch, ready=False, heal=boom, settings_mode="write")
    result = CliRunner().invoke(mcp, ["--database", "warpeace"])

    # A genuine migration error is swallowed; routing still happens on state.
    assert result.exit_code == 0, result.output
    spies["heal"].assert_called_once()
    spies["maint"].assert_called_once_with("warpeace")
    spies["normal"].assert_not_called()

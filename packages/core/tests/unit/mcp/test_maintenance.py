# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for MCP maintenance-mode tools and server."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chaoscypher_core.mcp.tools import (
    TOOL_DEFINITIONS,
    get_maintenance_tools,
    get_tools_for_mode,
)


# --------------------------------------------------------------------------- #
#  Task 1 — maintenance tool definitions
# --------------------------------------------------------------------------- #


def test_maintenance_tools_are_status_and_apply() -> None:
    names = {t.name for t in get_maintenance_tools()}
    assert names == {"upgrade_status", "apply_upgrade"}


def test_maintenance_tools_excluded_from_normal_toolset() -> None:
    normal = {t.name for t in TOOL_DEFINITIONS}
    assert "upgrade_status" not in normal
    assert "apply_upgrade" not in normal
    # ...and not surfaced by either access mode.
    for mode in ("read", "write"):
        mode_names = {t.name for t in get_tools_for_mode(mode)}
        assert "upgrade_status" not in mode_names
        assert "apply_upgrade" not in mode_names


def test_apply_upgrade_advertises_confirm_destructive() -> None:
    apply_tool = next(t for t in get_maintenance_tools() if t.name == "apply_upgrade")
    props = apply_tool.input_schema["properties"]
    assert "confirm_destructive" in props
    assert props["confirm_destructive"]["type"] == "boolean"


# --------------------------------------------------------------------------- #
#  Task 2 — maintenance server handlers + dispatch + factory
# --------------------------------------------------------------------------- #


def _parse(text_contents: list) -> dict:
    assert len(text_contents) == 1
    return json.loads(text_contents[0].text)


def test_upgrade_status_reports_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import chaoscypher_core.mcp.maintenance as m
    from chaoscypher_core.database.migrations.tiers import MigrationTier
    from chaoscypher_core.database.migrations.upgrade import (
        PendingMigration,
        PendingMigrationsResponse,
    )

    fake_pending = PendingMigrationsResponse(
        ready=False,
        blocked_on=[
            PendingMigration(
                revision="0042",
                tier=MigrationTier.MANUAL,
                description="Drops the legacy column and forces re-extraction.",
            )
        ],
        message="1 migration needs confirmation before the app can be used.",
        last_backup="/data/backups/pre-0042.db",
    )
    monkeypatch.setattr(m, "get_db_path", lambda _name: Path("/data/app.db"))
    monkeypatch.setattr(m.UpgradeService, "pending", lambda self: fake_pending)
    monkeypatch.setattr(m, "get_upgrade_state", lambda _p: SimpleNamespace(last_applied=["0041"]))

    result = m._handle_upgrade_status("warpeace")

    assert result["success"] is True
    assert result["ready"] is False
    assert result["blocked_on"][0]["revision"] == "0042"
    assert result["blocked_on"][0]["tier"] == "manual"
    assert result["last_backup"] == "/data/backups/pre-0042.db"
    assert result["last_applied"] == ["0041"]
    assert "needs confirmation" in result["message"]


def test_apply_upgrade_refuses_destructive_without_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chaoscypher_core.mcp.maintenance as m
    from chaoscypher_core.database.migrations.tiers import MigrationInfo, MigrationTier

    monkeypatch.setattr(m, "get_db_path", lambda _name: Path("/data/app.db"))
    monkeypatch.setattr(m, "pending_revisions", lambda _p: ["0042", "0043"])
    monkeypatch.setattr(
        m,
        "read_migration_info",
        lambda rev: MigrationInfo(
            rev,
            MigrationTier.MANUAL if rev == "0042" else MigrationTier.SAFE_AUTO,
            "",
        ),
    )

    def _boom(self):  # apply must NOT be called
        raise AssertionError("apply() should not run without confirm_destructive")

    monkeypatch.setattr(m.UpgradeService, "apply", _boom)

    result = m._handle_apply_upgrade("warpeace", confirm_destructive=False)

    assert result["success"] is False
    assert result["error_code"] == "CONFIRM_DESTRUCTIVE_REQUIRED"
    assert "0042" in result["destructive"]


def test_apply_upgrade_applies_when_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    import chaoscypher_core.mcp.maintenance as m
    from chaoscypher_core.database.migrations.tiers import MigrationInfo, MigrationTier
    from chaoscypher_core.database.migrations.upgrade import ApplyResponse

    monkeypatch.setattr(m, "get_db_path", lambda _name: Path("/data/app.db"))
    monkeypatch.setattr(m, "pending_revisions", lambda _p: ["0042"])
    monkeypatch.setattr(
        m, "read_migration_info", lambda rev: MigrationInfo(rev, MigrationTier.MANUAL, "")
    )
    monkeypatch.setattr(
        m.UpgradeService,
        "apply",
        lambda self: ApplyResponse(
            applied=["0042"],
            current_revision="0050",
            backup_path="/data/backups/pre-0042.db",
        ),
    )

    result = m._handle_apply_upgrade("warpeace", confirm_destructive=True)

    assert result["success"] is True
    assert result["applied"] == ["0042"]
    assert result["current_revision"] == "0050"
    assert "reconnect" in result["message"].lower()


def test_apply_upgrade_safe_only_no_confirm_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chaoscypher_core.mcp.maintenance as m
    from chaoscypher_core.database.migrations.tiers import MigrationInfo, MigrationTier
    from chaoscypher_core.database.migrations.upgrade import ApplyResponse

    monkeypatch.setattr(m, "get_db_path", lambda _name: Path("/data/app.db"))
    monkeypatch.setattr(m, "pending_revisions", lambda _p: ["0048"])
    monkeypatch.setattr(
        m, "read_migration_info", lambda rev: MigrationInfo(rev, MigrationTier.SAFE_AUTO, "")
    )
    monkeypatch.setattr(
        m.UpgradeService,
        "apply",
        lambda self: ApplyResponse(applied=["0048"], current_revision="0050", backup_path=None),
    )

    result = m._handle_apply_upgrade("warpeace", confirm_destructive=False)

    assert result["success"] is True
    assert result["applied"] == ["0048"]


def test_apply_upgrade_no_pending_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    import chaoscypher_core.mcp.maintenance as m

    monkeypatch.setattr(m, "get_db_path", lambda _name: Path("/data/app.db"))
    monkeypatch.setattr(m, "pending_revisions", lambda _p: [])

    result = m._handle_apply_upgrade("warpeace", confirm_destructive=False)

    assert result["success"] is True
    assert result["applied"] == []
    assert "reconnect" in result["message"].lower()


def test_apply_upgrade_handles_apply_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import chaoscypher_core.mcp.maintenance as m
    from chaoscypher_core.database.migrations.tiers import MigrationInfo, MigrationTier

    monkeypatch.setattr(m, "get_db_path", lambda _name: Path("/data/app.db"))
    monkeypatch.setattr(m, "pending_revisions", lambda _p: ["0048"])
    monkeypatch.setattr(
        m, "read_migration_info", lambda rev: MigrationInfo(rev, MigrationTier.SAFE_AUTO, "")
    )
    # Stub get_upgrade_state so the failure path that reads last_backup does not
    # touch the real /data dir (get_db_path is faked to /data/app.db, and
    # get_upgrade_state would otherwise try to create/open it — denied on CI).
    monkeypatch.setattr(m, "get_upgrade_state", lambda _p: SimpleNamespace(last_backup=None))

    def _raise(self):
        raise RuntimeError("disk full")

    monkeypatch.setattr(m.UpgradeService, "apply", _raise)

    result = m._handle_apply_upgrade("warpeace", confirm_destructive=False)

    assert result["success"] is False
    assert result["error_code"] == "UPGRADE_FAILED"


def test_apply_upgrade_failure_surfaces_schema_ahead_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed apply must surface the REAL cause, not a generic 'check logs'.

    When the schema is ahead of its stamp, apply re-raises the SQLite
    'duplicate column' error. The agent calling apply_upgrade should see
    WHY it keeps failing (and that the data is safe), not a dead-end.
    """
    import chaoscypher_core.mcp.maintenance as m
    from chaoscypher_core.database.migrations.tiers import MigrationInfo, MigrationTier

    monkeypatch.setattr(m, "get_db_path", lambda _name: Path("/data/app.db"))
    monkeypatch.setattr(m, "pending_revisions", lambda _p: ["0010"])
    monkeypatch.setattr(
        m, "read_migration_info", lambda rev: MigrationInfo(rev, MigrationTier.SAFE_AUTO, "")
    )
    monkeypatch.setattr(
        m,
        "get_upgrade_state",
        lambda _p: SimpleNamespace(last_backup="/data/backups/pre-0010.db"),
    )

    def _raise(self):
        raise RuntimeError(
            "(sqlite3.OperationalError) duplicate column name: cached_structural_penalty"
        )

    monkeypatch.setattr(m.UpgradeService, "apply", _raise)

    result = m._handle_apply_upgrade("warpeace", confirm_destructive=False)

    assert result["success"] is False
    assert result["error_code"] == "UPGRADE_FAILED"
    # The real cause, in plain language, plus the underlying error text.
    assert "recorded version is behind" in result["error"]
    assert "duplicate column" in result["error"]


@pytest.mark.asyncio
async def test_dispatch_blocked_tool_returns_actionable_error() -> None:
    import chaoscypher_core.mcp.maintenance as m

    out = _parse(await m._maintenance_dispatch("warpeace", "graphrag_search", {"query": "x"}))

    assert out["success"] is False
    assert out["error_code"] == "DATABASE_UPGRADE_REQUIRED"
    assert "apply_upgrade" in out["error"]


@pytest.mark.asyncio
async def test_dispatch_routes_upgrade_status(monkeypatch: pytest.MonkeyPatch) -> None:
    import chaoscypher_core.mcp.maintenance as m

    monkeypatch.setattr(m, "_handle_upgrade_status", lambda name: {"success": True, "routed": name})

    out = _parse(await m._maintenance_dispatch("warpeace", "upgrade_status", None))

    assert out == {"success": True, "routed": "warpeace"}


@pytest.mark.asyncio
async def test_dispatch_routes_apply_upgrade_with_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chaoscypher_core.mcp.maintenance as m

    captured: dict = {}

    def _fake(name: str, *, confirm_destructive: bool) -> dict:
        captured["name"] = name
        captured["confirm"] = confirm_destructive
        return {"success": True}

    monkeypatch.setattr(m, "_handle_apply_upgrade", _fake)

    await m._maintenance_dispatch("warpeace", "apply_upgrade", {"confirm_destructive": True})

    assert captured == {"name": "warpeace", "confirm": True}


def test_tool_list_has_two_maintenance_tools() -> None:
    import chaoscypher_core.mcp.maintenance as m

    tools = m._maintenance_tool_list()
    assert {t.name for t in tools} == {"upgrade_status", "apply_upgrade"}


def test_create_maintenance_server_returns_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp.server import Server

    import chaoscypher_core.mcp.maintenance as m

    monkeypatch.setattr(m, "get_db_path", lambda _name: Path("/data/app.db"))
    monkeypatch.setattr(m, "get_upgrade_state", lambda _p: SimpleNamespace(blocked_on=["0042"]))

    server = m.create_maintenance_mcp_server("warpeace")
    assert isinstance(server, Server)

# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""`chaoscypher upgrade` applies pending migrations via Core's UpgradeService.

Regression: the command used to shell out to ``python -m alembic upgrade
head`` with no ``-c`` and no DB targeting — the packaged alembic.ini isn't on
the CWD and its ``sqlalchemy.url`` is blank, so the command always failed and
targeted no specific database. It now routes through the same
``UpgradeService`` the Cortex ``/upgrade`` API and ``db migrate apply`` use,
against the resolved database.
"""

from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

import chaoscypher_cli.commands.upgrade as upgrade_module
import chaoscypher_core.database.migrations.upgrade as upgrade_service_mod
from chaoscypher_cli.__main__ import main
from chaoscypher_cli.commands.upgrade import upgrade_command
from chaoscypher_core.database.migrations.tiers import MigrationTier
from chaoscypher_core.database.migrations.upgrade import (
    PendingMigration,
    PendingMigrationsResponse,
)


_HOLD = ['"Work Docs" → "work-docs"', '"[b]old" → "b-old"']


class _HeldService:
    """Stand-in whose pending 0026 holds: it would rename tags."""

    applied: bool = False

    def __init__(self, database_name: str) -> None:
        _HeldService.applied = False

    def pending(self) -> PendingMigrationsResponse:
        return PendingMigrationsResponse(
            ready=False,
            blocked_on=[
                PendingMigration(
                    revision="0026",
                    tier=MigrationTier.NEEDS_CONFIRMATION,
                    description="Tag names become lower-case",
                    hold=list(_HOLD),
                )
            ],
        )

    def apply(self) -> SimpleNamespace:
        _HeldService.applied = True
        return SimpleNamespace(applied=["0026"], current_revision="0026", backup_path=None)


class _FakeService:
    """Stand-in UpgradeService capturing the DB it targeted and apply calls."""

    last_database: str | None = None
    applied: bool = False

    def __init__(self, database_name: str) -> None:
        _FakeService.last_database = database_name
        _FakeService.applied = False

    def pending(self) -> PendingMigrationsResponse:
        return PendingMigrationsResponse(
            ready=False,
            blocked_on=[
                PendingMigration(revision="0046", tier=MigrationTier.SAFE_AUTO, description="x")
            ],
        )

    def apply(self) -> SimpleNamespace:
        _FakeService.applied = True
        return SimpleNamespace(applied=["0046"], current_revision="0046", backup_path=None)


class _AtHeadService:
    """Stand-in whose DB is already at head — apply must NOT be called."""

    applied: bool = False

    def __init__(self, database_name: str) -> None:
        _AtHeadService.applied = False

    def pending(self) -> PendingMigrationsResponse:
        return PendingMigrationsResponse(ready=True)

    def apply(self) -> SimpleNamespace:  # pragma: no cover - must not run
        _AtHeadService.applied = True
        raise AssertionError("apply() should not be called when already at head")


def test_upgrade_applies_pending_via_service(monkeypatch) -> None:
    monkeypatch.setattr(upgrade_service_mod, "UpgradeService", _FakeService)

    result = CliRunner().invoke(upgrade_command, ["--database", "research"])

    assert result.exit_code == 0, result.output
    assert _FakeService.last_database == "research", "command targeted the wrong database"
    assert _FakeService.applied is True, "pending migrations were not applied"
    assert "0046" in result.output


def test_upgrade_noop_when_at_head(monkeypatch) -> None:
    monkeypatch.setattr(upgrade_service_mod, "UpgradeService", _AtHeadService)

    result = CliRunner().invoke(upgrade_command, ["--database", "research"])

    assert result.exit_code == 0, result.output
    assert _AtHeadService.applied is False
    assert "head" in result.output.lower()


def test_upgrade_does_not_shell_out_to_alembic() -> None:
    """The broken subprocess wrapper is gone — no _run_alembic on the module."""
    assert not hasattr(upgrade_module, "_run_alembic"), (
        "upgrade command still shells out to `python -m alembic` instead of UpgradeService"
    )


def test_upgrade_registered_on_main() -> None:
    """The 'upgrade' subcommand is listed in the top-level CLI help."""
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert "upgrade" in result.output


def test_upgrade_builds_a_new_database_without_a_backup_or_record(isolated_settings) -> None:
    """A first install is not an upgrade: no pre-upgrade backup, nothing to roll back."""
    from chaoscypher_core.database.engine import get_db_path
    from chaoscypher_core.database.migrations.runner import current_revision, head_revision
    from chaoscypher_core.database.migrations.state import UpgradeState, get_upgrade_state

    result = CliRunner().invoke(upgrade_command, ["--database", "brandnew"])

    assert result.exit_code == 0, result.output
    db = get_db_path("brandnew")
    assert current_revision(db) == head_revision()
    assert get_upgrade_state(db) == UpgradeState(ready=True)
    assert not (db.parent / "backups").exists()


def test_upgrade_lists_the_hold_lines_and_applies_nothing_when_declined(monkeypatch) -> None:
    """A held migration is never applied unseen: upgrade lists its changes and asks."""
    monkeypatch.setattr(upgrade_service_mod, "UpgradeService", _HeldService)

    result = CliRunner().invoke(upgrade_command, ["--database", "research"], input="n\n")

    assert result.exit_code == 1, result.output
    for line in _HOLD:
        assert line in result.output
    assert _HeldService.applied is False


def test_upgrade_yes_applies_a_held_migration(monkeypatch) -> None:
    monkeypatch.setattr(upgrade_service_mod, "UpgradeService", _HeldService)

    result = CliRunner().invoke(upgrade_command, ["--database", "research", "--yes"])

    assert result.exit_code == 0, result.output
    assert _HeldService.applied is True

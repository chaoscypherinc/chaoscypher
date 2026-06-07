# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CliRunner coverage for ``chaoscypher db migrate`` (status/apply/rollback).

This command wraps Alembic via ``UpgradeService`` (core). NO real migration
ever runs here: the service is mocked so we only assert wiring — which DB it
targets, what it prints, and the exit codes for the up-to-date / pending /
error paths.

Two seams are patched:

- ``_service`` is patched to return a configured ``MagicMock`` so the
  status/apply/rollback bodies run against canned, REAL Pydantic response
  DTOs (``PendingMigrationsResponse`` etc.) — so ``model_dump_json`` and
  attribute access behave exactly as in production.
- For the "right DB resolved" assertion we instead patch the lazily-imported
  ``UpgradeService`` constructor itself and check the ``database_name`` arg,
  and patch ``get_database_name`` so ``_resolve_database`` falls back
  correctly (the current db lives in settings.yaml as of the 2026-06 config
  unification).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.commands.db.migrate import _resolve_database, _service, migrate
from chaoscypher_core.database.migrations.tiers import MigrationTier
from chaoscypher_core.database.migrations.upgrade import (
    ApplyResponse,
    PendingMigration,
    PendingMigrationsResponse,
    RollbackResponse,
)


# ---------------------------------------------------------------------------
# Helpers — build REAL Pydantic DTOs so model_dump_json / attrs behave
# ---------------------------------------------------------------------------


def _pending_resp(
    *,
    ready: bool = True,
    blocked: list[PendingMigration] | None = None,
    message: str = "",
    last_backup: str | None = None,
) -> PendingMigrationsResponse:
    return PendingMigrationsResponse(
        ready=ready,
        blocked_on=blocked or [],
        message=message,
        last_backup=last_backup,
    )


def _migration(
    revision: str,
    tier: MigrationTier = MigrationTier.SAFE_AUTO,
    description: str = "desc",
) -> PendingMigration:
    return PendingMigration(revision=revision, tier=tier, description=description)


def _service_mock(pending: PendingMigrationsResponse) -> MagicMock:
    svc = MagicMock()
    svc.pending.return_value = pending
    return svc


# ===========================================================================
# _resolve_database / _service helpers
# ===========================================================================


class TestResolveDatabase:
    """``_resolve_database`` prefers the explicit arg, else the current db."""

    def test_explicit_database_wins(self) -> None:
        assert _resolve_database("explicit") == "explicit"

    def test_falls_back_to_current(self) -> None:
        with patch(
            "chaoscypher_cli.commands.db.migrate.get_database_name",
            return_value="active_db",
        ):
            assert _resolve_database(None) == "active_db"

    def test_service_constructs_upgrade_service_with_resolved_db(self) -> None:
        """``_service`` builds an UpgradeService bound to the resolved DB."""
        with patch(
            "chaoscypher_cli.commands.db.migrate.get_database_name",
            return_value="active_db",
        ):
            with patch("chaoscypher_core.database.migrations.upgrade.UpgradeService") as mock_cls:
                _service(None)
        mock_cls.assert_called_once_with(database_name="active_db")

    def test_service_forwards_explicit_database(self) -> None:
        with patch("chaoscypher_core.database.migrations.upgrade.UpgradeService") as mock_cls:
            _service("explicit")
        mock_cls.assert_called_once_with(database_name="explicit")


# ===========================================================================
# status
# ===========================================================================


class TestStatus:
    """``migrate status`` — up to date, pending list, and JSON."""

    def test_status_up_to_date(self) -> None:
        runner = CliRunner()
        svc = _service_mock(_pending_resp(ready=True))

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["status"])

        assert result.exit_code == 0, result.output
        assert "No pending migrations" in result.output
        assert "up to date" in result.output

    def test_status_lists_pending(self) -> None:
        runner = CliRunner()
        svc = _service_mock(
            _pending_resp(
                ready=False,
                blocked=[
                    _migration("0007_abc", MigrationTier.NEEDS_CONFIRMATION, "merge dupes"),
                    _migration("0008_def", MigrationTier.SAFE_AUTO, "add column"),
                ],
                message="Operator action required.",
                last_backup="/backups/pre-0007.db",
            )
        )

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["status"])

        assert result.exit_code == 0, result.output
        assert "Blocked" in result.output
        assert "Operator action required." in result.output
        assert "/backups/pre-0007.db" in result.output
        assert "0007_abc" in result.output
        assert "0008_def" in result.output
        assert "merge dupes" in result.output

    def test_status_ready_but_blocked_renders_table(self) -> None:
        """ready=True with a non-empty blocked_on still renders the table."""
        runner = CliRunner()
        svc = _service_mock(
            _pending_resp(
                ready=True,
                blocked=[_migration("0009_xyz", MigrationTier.MANUAL, "manual step")],
            )
        )

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["status"])

        assert result.exit_code == 0, result.output
        assert "Ready" in result.output
        assert "0009_xyz" in result.output
        assert "manual step" in result.output

    def test_status_json(self) -> None:
        runner = CliRunner()
        svc = _service_mock(
            _pending_resp(
                ready=False,
                blocked=[_migration("0007_abc", MigrationTier.SAFE_AUTO, "add col")],
                message="msg",
            )
        )

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["status", "--json"])

        assert result.exit_code == 0, result.output
        import json

        data = json.loads(result.output)
        assert data["ready"] is False
        assert data["blocked_on"][0]["revision"] == "0007_abc"
        assert data["message"] == "msg"

    def test_status_targets_database_flag(self) -> None:
        """--database is forwarded to _service."""
        runner = CliRunner()
        svc = _service_mock(_pending_resp(ready=True))

        with patch(
            "chaoscypher_cli.commands.db.migrate._service", return_value=svc
        ) as mock_service:
            result = runner.invoke(migrate, ["status", "--database", "myproj"])

        assert result.exit_code == 0, result.output
        mock_service.assert_called_once_with("myproj")


# ===========================================================================
# apply
# ===========================================================================


class TestApply:
    """``migrate apply`` — nothing to do, safe-auto, confirm gate, errors."""

    def test_apply_nothing_to_do(self) -> None:
        runner = CliRunner()
        svc = _service_mock(_pending_resp(ready=True))

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["apply"])

        assert result.exit_code == 0, result.output
        assert "Nothing to apply" in result.output
        svc.apply.assert_not_called()

    def test_apply_safe_auto_applies_without_prompt(self) -> None:
        runner = CliRunner()
        svc = _service_mock(
            _pending_resp(
                ready=False,
                blocked=[_migration("0008_def", MigrationTier.SAFE_AUTO, "add col")],
            )
        )
        svc.apply.return_value = ApplyResponse(
            applied=["0008_def"],
            current_revision="0008_def",
            backup_path=None,
        )

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["apply"])

        assert result.exit_code == 0, result.output
        assert "Applied 1 migration(s)" in result.output
        assert "0008_def" in result.output
        assert "Now at revision:" in result.output
        svc.apply.assert_called_once()

    def test_apply_needs_confirmation_prompts_and_proceeds_on_yes(self) -> None:
        runner = CliRunner()
        svc = _service_mock(
            _pending_resp(
                ready=False,
                blocked=[_migration("0007_abc", MigrationTier.NEEDS_CONFIRMATION, "merge dupes")],
                last_backup="/backups/pre-0007.db",
            )
        )
        svc.apply.return_value = ApplyResponse(
            applied=["0007_abc"],
            current_revision="0007_abc",
            backup_path="/backups/pre-0007.db",
        )

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["apply"], input="y\n")

        assert result.exit_code == 0, result.output
        assert "need confirmation" in result.output
        assert "Pre-upgrade backup:" in result.output
        svc.apply.assert_called_once()

    def test_apply_needs_confirmation_aborts_on_no(self) -> None:
        runner = CliRunner()
        svc = _service_mock(
            _pending_resp(
                ready=False,
                blocked=[_migration("0007_abc", MigrationTier.NEEDS_CONFIRMATION, "merge dupes")],
            )
        )

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["apply"], input="n\n")

        # click.confirm(abort=True) -> Abort -> exit code 1, apply never runs.
        assert result.exit_code == 1
        svc.apply.assert_not_called()

    def test_apply_yes_skips_confirmation(self) -> None:
        runner = CliRunner()
        svc = _service_mock(
            _pending_resp(
                ready=False,
                blocked=[_migration("0007_abc", MigrationTier.NEEDS_CONFIRMATION, "merge dupes")],
            )
        )
        svc.apply.return_value = ApplyResponse(
            applied=["0007_abc"],
            current_revision="0007_abc",
            backup_path=None,
        )

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            # No stdin provided; --yes must mean no prompt is shown.
            result = runner.invoke(migrate, ["apply", "--yes"])

        assert result.exit_code == 0, result.output
        svc.apply.assert_called_once()
        assert "Apply all pending migrations?" not in result.output

    def test_apply_error_path_exits_1_with_rollback_hint(self) -> None:
        runner = CliRunner()
        svc = _service_mock(
            _pending_resp(
                ready=False,
                blocked=[_migration("0008_def", MigrationTier.SAFE_AUTO, "add col")],
                last_backup="/backups/pre-0008.db",
            )
        )
        svc.apply.side_effect = RuntimeError("alembic boom")

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["apply", "--database", "myproj"])

        assert result.exit_code == 1
        assert "Migration failed:" in result.output
        assert "alembic boom" in result.output
        # Rollback hint is shown because a backup exists; --database wins so the
        # hint targets "myproj" without consulting settings.yaml.
        assert "rollback" in result.output
        assert "myproj" in result.output

    def test_apply_error_path_no_backup_no_hint(self) -> None:
        runner = CliRunner()
        svc = _service_mock(
            _pending_resp(
                ready=False,
                blocked=[_migration("0008_def", MigrationTier.SAFE_AUTO, "add col")],
                last_backup=None,
            )
        )
        svc.apply.side_effect = RuntimeError("alembic boom")

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["apply"])

        assert result.exit_code == 1
        assert "Migration failed:" in result.output
        assert "Roll back with" not in result.output


# ===========================================================================
# rollback
# ===========================================================================


class TestRollback:
    """``migrate rollback`` — no backup, confirm gate, success, error."""

    def test_rollback_no_backup_exits_1(self) -> None:
        runner = CliRunner()
        svc = _service_mock(_pending_resp(ready=True, last_backup=None))

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["rollback"])

        assert result.exit_code == 1
        assert "No pre-upgrade backup available" in result.output
        svc.rollback.assert_not_called()

    def test_rollback_confirm_yes_restores(self) -> None:
        runner = CliRunner()
        svc = _service_mock(_pending_resp(ready=False, last_backup="/backups/pre.db"))
        svc.rollback.return_value = RollbackResponse(
            restored_from="/backups/pre.db",
            revision="0006_prev",
        )

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["rollback"], input="y\n")

        assert result.exit_code == 0, result.output
        assert "Restored from:" in result.output
        assert "/backups/pre.db" in result.output
        assert "0006_prev" in result.output
        svc.rollback.assert_called_once()

    def test_rollback_confirm_no_aborts(self) -> None:
        runner = CliRunner()
        svc = _service_mock(_pending_resp(ready=False, last_backup="/backups/pre.db"))

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["rollback"], input="n\n")

        assert result.exit_code == 1  # Abort
        svc.rollback.assert_not_called()

    def test_rollback_yes_skips_prompt(self) -> None:
        runner = CliRunner()
        svc = _service_mock(_pending_resp(ready=False, last_backup="/backups/pre.db"))
        svc.rollback.return_value = RollbackResponse(
            restored_from="/backups/pre.db",
            revision="0006_prev",
        )

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["rollback", "--yes"])

        assert result.exit_code == 0, result.output
        svc.rollback.assert_called_once()
        assert "Proceed with rollback?" not in result.output

    def test_rollback_error_path_exits_1(self) -> None:
        runner = CliRunner()
        svc = _service_mock(_pending_resp(ready=False, last_backup="/backups/pre.db"))
        svc.rollback.side_effect = FileNotFoundError("backup gone")

        with patch("chaoscypher_cli.commands.db.migrate._service", return_value=svc):
            result = runner.invoke(migrate, ["rollback", "--yes"])

        assert result.exit_code == 1
        assert "Rollback failed:" in result.output
        assert "backup gone" in result.output


# ===========================================================================
# registration
# ===========================================================================


def test_migrate_group_has_three_subcommands() -> None:
    assert set(migrate.commands) == {"status", "apply", "rollback"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

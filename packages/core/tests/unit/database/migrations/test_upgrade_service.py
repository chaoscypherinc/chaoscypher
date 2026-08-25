# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for UpgradeService.pending() surfacing the post-success record."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_pending_surfaces_silent_upgrade_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.database.migrations import upgrade as upgrade_mod
    from chaoscypher_core.database.migrations.state import record_successful_upgrade

    db = tmp_path / "app.db"
    record_successful_upgrade(
        db, applied=["0042", "0043"], last_backup="/b/pre-0042.db", data_changing=True
    )
    monkeypatch.setattr(upgrade_mod, "get_db_path", lambda _name: db)
    monkeypatch.setattr(upgrade_mod, "pending_revisions", lambda _p: [])

    resp = upgrade_mod.UpgradeService("test").pending()

    assert resp.ready is True
    assert resp.blocked_on == []
    assert resp.last_applied == ["0042", "0043"]
    assert resp.data_changing is True
    assert resp.last_backup == "/b/pre-0042.db"


def test_pending_after_clear_has_no_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chaoscypher_core.database.migrations import upgrade as upgrade_mod
    from chaoscypher_core.database.migrations.state import clear_upgrade_state

    db = tmp_path / "app.db"
    clear_upgrade_state(db)
    monkeypatch.setattr(upgrade_mod, "get_db_path", lambda _name: db)
    monkeypatch.setattr(upgrade_mod, "pending_revisions", lambda _p: [])

    resp = upgrade_mod.UpgradeService("test").pending()

    assert resp.last_applied == []
    assert resp.data_changing is False


def test_apply_failure_still_persists_fresh_backup_and_rollback_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh backup taken by apply() must be durable BEFORE upgrade_to_head.

    If the upgrade raises, rollback() reads only the state row — a backup
    path held in a local variable would be lost and the operator told
    "No backup available" while the backup sits on disk (2026-08-07 audit).
    """
    import shutil
    from types import SimpleNamespace

    from chaoscypher_core.database.migrations import upgrade as upgrade_mod
    from chaoscypher_core.database.migrations.state import (
        clear_upgrade_state,
        get_upgrade_state,
    )

    db = tmp_path / "app.db"
    clear_upgrade_state(db)  # creates the state table; last_backup=None
    backup_file = tmp_path / "pre-0042.db"

    def fake_backup(path: Path, label: str) -> SimpleNamespace:
        shutil.copy2(path, backup_file)
        return SimpleNamespace(backup_path=backup_file)

    def failing_upgrade(_path: Path) -> None:
        msg = "boom mid-upgrade"
        raise RuntimeError(msg)

    monkeypatch.setattr(upgrade_mod, "get_db_path", lambda _name: db)
    monkeypatch.setattr(upgrade_mod, "pending_revisions", lambda _p: ["0042"])
    monkeypatch.setattr(upgrade_mod, "backup_database", fake_backup)
    monkeypatch.setattr(upgrade_mod, "upgrade_to_head", failing_upgrade)
    monkeypatch.setattr(upgrade_mod, "current_revision", lambda _p: "0041")

    service = upgrade_mod.UpgradeService("test")
    with pytest.raises(RuntimeError, match="boom mid-upgrade"):
        service.apply()

    # The backup path survived the failure...
    assert get_upgrade_state(db).last_backup == str(backup_file)

    # ...and rollback() can actually use it.
    resp = service.rollback()
    assert resp.restored_from == str(backup_file)


def _setup_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, tier: object
) -> tuple[object, Path, Path]:
    """Wire an UpgradeService whose apply() succeeds with one pending revision."""
    import shutil
    from types import SimpleNamespace

    from chaoscypher_core.database.migrations import upgrade as upgrade_mod
    from chaoscypher_core.database.migrations.state import clear_upgrade_state

    db = tmp_path / "app.db"
    clear_upgrade_state(db)  # creates the state table; last_backup=None
    backup_file = tmp_path / "pre-0042.db"

    def fake_backup(path: Path, label: str) -> SimpleNamespace:
        shutil.copy2(path, backup_file)
        return SimpleNamespace(backup_path=backup_file)

    monkeypatch.setattr(upgrade_mod, "get_db_path", lambda _name: db)
    monkeypatch.setattr(upgrade_mod, "pending_revisions", lambda _p: ["0042"])
    monkeypatch.setattr(upgrade_mod, "backup_database", fake_backup)
    monkeypatch.setattr(upgrade_mod, "upgrade_to_head", lambda _p: None)
    monkeypatch.setattr(upgrade_mod, "current_revision", lambda _p: "0042")
    monkeypatch.setattr(
        upgrade_mod,
        "read_migration_info",
        lambda rev: SimpleNamespace(revision=rev, tier=tier, description="d"),
    )
    return upgrade_mod.UpgradeService("test"), db, backup_file


def test_successful_data_changing_apply_retains_backup_and_rollback_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A data-changing apply keeps last_backup so rollback stays possible."""
    from chaoscypher_core.database.migrations.state import get_upgrade_state
    from chaoscypher_core.database.migrations.tiers import MigrationTier

    service, db, backup_file = _setup_apply(
        tmp_path, monkeypatch, tier=MigrationTier.NEEDS_CONFIRMATION
    )

    resp = service.apply()

    assert resp.applied == ["0042"]
    state = get_upgrade_state(db)
    assert state.ready is True
    assert state.last_backup == str(backup_file)
    assert state.last_applied == ["0042"]
    assert state.data_changing is True

    rollback = service.rollback()
    assert rollback.restored_from == str(backup_file)


def test_successful_safe_auto_apply_still_clears_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SAFE_AUTO-only apply clears the state row as before."""
    from chaoscypher_core.database.migrations.state import get_upgrade_state
    from chaoscypher_core.database.migrations.tiers import MigrationTier

    service, db, _backup_file = _setup_apply(tmp_path, monkeypatch, tier=MigrationTier.SAFE_AUTO)

    resp = service.apply()

    assert resp.applied == ["0042"]
    state = get_upgrade_state(db)
    assert state.ready is True
    assert state.last_backup is None
    assert state.last_applied == []
    assert state.data_changing is False

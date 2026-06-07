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


def test_pending_after_clear_has_no_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core.database.migrations import upgrade as upgrade_mod
    from chaoscypher_core.database.migrations.state import clear_upgrade_state

    db = tmp_path / "app.db"
    clear_upgrade_state(db)
    monkeypatch.setattr(upgrade_mod, "get_db_path", lambda _name: db)
    monkeypatch.setattr(upgrade_mod, "pending_revisions", lambda _p: [])

    resp = upgrade_mod.UpgradeService("test").pending()

    assert resp.last_applied == []
    assert resp.data_changing is False

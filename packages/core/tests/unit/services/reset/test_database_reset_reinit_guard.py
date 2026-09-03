# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``DatabaseResetService.reset_all`` reinitialization guard.

A filesystem failure inside the destructive section (unlink/rmtree) must
still reinitialize the database before the exception propagates to the
queue task — otherwise the app is left pointing at a nonexistent schema
with no recovery short of a process restart.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.reset import database_reset as dbr_mod
from chaoscypher_core.services.reset.database_reset import DatabaseResetService


def _make_service(tmp_path: Path) -> DatabaseResetService:
    """Build a service wired to tmp paths, with a graphs/ dir so rmtree runs."""
    settings = MagicMock()
    settings.paths.data_dir = str(tmp_path)
    settings.paths.databases_subdir = "databases"

    db_dir = tmp_path / "databases" / "testdb"
    (db_dir / "graphs").mkdir(parents=True)
    (db_dir / "graphs" / "g.json").write_text("{}")

    with patch.object(dbr_mod, "get_settings", return_value=settings):
        return DatabaseResetService("testdb")


@pytest.mark.asyncio
async def test_rmtree_failure_reinitializes_before_propagating(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    init_mock = MagicMock()

    with (
        patch.object(
            dbr_mod, "get_db_path", return_value=tmp_path / "databases" / "testdb" / "app.db"
        ),
        patch.object(dbr_mod, "evict_engine"),
        patch.object(dbr_mod, "init_database", init_mock),
        patch.object(dbr_mod.shutil, "rmtree", side_effect=OSError("read-only mount")),
        pytest.raises(OSError, match="read-only mount"),
    ):
        await service.reset_all()

    init_mock.assert_called_once_with("testdb")


@pytest.mark.asyncio
async def test_reinit_failure_does_not_mask_original_error(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    with (
        patch.object(
            dbr_mod, "get_db_path", return_value=tmp_path / "databases" / "testdb" / "app.db"
        ),
        patch.object(dbr_mod, "evict_engine"),
        patch.object(dbr_mod, "init_database", side_effect=RuntimeError("init also broken")),
        patch.object(dbr_mod.shutil, "rmtree", side_effect=OSError("read-only mount")),
        pytest.raises(OSError, match="read-only mount"),
    ):
        await service.reset_all()

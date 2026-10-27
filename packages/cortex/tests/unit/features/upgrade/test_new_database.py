# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The upgrade API on a real database: a new one offers nothing to roll back, an old one does.

The web app's post-upgrade notice and the Maintenance page's rollback read
``GET /upgrade/pending``; a first install must answer with no backup, no applied
revisions and nothing data-changing, while an upgraded database keeps all three.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_core.database.engine import get_db_path, init_database
from chaoscypher_core.database.migrations.runner import (
    head_revision,
    pending_revisions,
    upgrade_to,
)
from chaoscypher_core.database.migrations.startup import run_startup_migrations
from chaoscypher_cortex.features.upgrade.api import router


if TYPE_CHECKING:
    from pathlib import Path


NAME = "firstrun"

NOTHING_TO_ROLL_BACK = {
    "ready": True,
    "blocked_on": [],
    "message": "",
    "last_backup": None,
    "last_applied": [],
    "data_changing": False,
}


@pytest.fixture
def client() -> TestClient:
    """The upgrade router bound to ``NAME`` in the test's data directory (dev mode: no login)."""
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: Settings(dev_mode=True, current_database=NAME)
    app.include_router(router)
    return TestClient(app)


def _backups(db: Path) -> list[Path]:
    folder = db.parent / "backups"
    return sorted(folder.iterdir()) if folder.exists() else []


def test_pending_for_a_new_database_offers_nothing_to_roll_back(client: TestClient) -> None:
    init_database(NAME)

    resp = client.get("/upgrade/pending")

    assert resp.status_code == 200
    assert resp.json() == NOTHING_TO_ROLL_BACK
    assert _backups(get_db_path(NAME)) == []


def test_apply_to_a_database_not_built_yet_builds_it_without_a_backup(
    client: TestClient,
) -> None:
    resp = client.post("/upgrade/apply")

    assert resp.status_code == 200
    body = resp.json()
    assert body["backup_path"] is None
    assert body["current_revision"] == head_revision()
    assert client.get("/upgrade/pending").json() == NOTHING_TO_ROLL_BACK
    assert _backups(get_db_path(NAME)) == []


def test_pending_after_a_real_upgrade_still_offers_the_rollback(client: TestClient) -> None:
    db = get_db_path(NAME)
    upgrade_to(db, "0018")
    expected = pending_revisions(db)
    run_startup_migrations(db, auto_apply_destructive=True)

    body = client.get("/upgrade/pending").json()

    assert body["ready"] is True
    assert body["data_changing"] is True
    assert body["last_applied"] == expected
    assert body["last_backup"] is not None
    assert [str(path) for path in _backups(db)] == [body["last_backup"]]

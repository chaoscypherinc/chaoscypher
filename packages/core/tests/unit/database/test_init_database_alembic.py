# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract: init_database leaves the DB stamped at Alembic HEAD.

Covers the Phase 2 wiring: after init_database returns, the database
file exists AND Alembic's current_revision matches head_revision. This
pins that the Alembic adoption step runs unconditionally as part of
the init flow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core import app_config
from chaoscypher_core.database.engine import init_database
from chaoscypher_core.database.migrations.runner import current_revision, head_revision


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CHAOSCYPHER_DATA_DIR at a temp dir and reset the settings singleton.

    ``init_database`` reads the resolved DB path via ``get_settings()``,
    which is decorated ``@lru_cache`` AND backed by a module-level
    ``_settings`` global in ``chaoscypher_core.app_config``. Both must be
    invalidated for the env-var override to take effect — clearing only
    one leaves the cached Settings in place and the test silently
    operates on the user's real data dir whenever a sibling test has
    already triggered ``get_settings()``.

    We restore both on teardown so the next test sees a clean cache too.
    """
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    # Force settings reload under the new env: clear lru_cache AND the
    # backing module global. monkeypatch.setattr handles restoring
    # _settings; we manually clear the lru_cache on entry and exit.
    monkeypatch.setattr(app_config, "_settings", None)
    app_config.get_settings.cache_clear()
    yield tmp_path
    app_config.get_settings.cache_clear()


def test_init_database_stamps_at_head_and_is_idempotent(
    isolated_data_dir: Path,
) -> None:
    """init_database runs the fresh-install path to HEAD and is idempotent.

    Fresh data dir → no tables exist → run_startup_migrations detects
    a truly fresh install and upgrades straight to head, bypassing the
    tier gate (no user data to protect from dedup).

    Kept as a single test because SQLModel.metadata is a module-global
    registry — a second test function in the same session would see state
    mutated by Alembic's env.py in the first test's run, which makes a
    two-function split brittle without per-test metadata reset.
    """
    db_path = isolated_data_dir / "databases" / "default" / "app.db"

    # First call: fresh data dir → full migration chain to HEAD.
    init_database("default")
    assert db_path.exists(), "init_database did not create the DB file"
    rev_after_first = current_revision(db_path)
    assert rev_after_first == head_revision(), (
        f"first init_database call did not reach HEAD: {rev_after_first!r} "
        f"(expected {head_revision()!r})"
    )

    # Second call: same data dir → idempotent, revision unchanged.
    init_database("default")
    rev_after_second = current_revision(db_path)
    assert rev_after_second == rev_after_first, (
        f"second init_database call changed revision: {rev_after_first!r} → {rev_after_second!r}"
    )

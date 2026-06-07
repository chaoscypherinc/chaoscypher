# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract: init_database degrades to maintenance instead of crashing the boot.

When ``run_startup_migrations`` leaves the DB blocked — a needs-confirmation
gate, or a failed apply because the live schema is ahead of its recorded
stamp (an interrupted upgrade) — ``init_database`` must NOT run the
post-migration housekeeping steps that assume an at-head schema. Running
them would re-crash the boot (the cortex/neuron process dies before
``uvicorn.run`` can serve the maintenance page), defeating the whole point
of the maintenance flow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core import app_config
from chaoscypher_core.database.engine import init_database
from chaoscypher_core.database.migrations import drift as drift_mod
from chaoscypher_core.database.migrations.state import (
    get_upgrade_state,
    set_upgrade_state,
)


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CHAOSCYPHER_DATA_DIR at a temp dir and reset the settings singleton.

    Mirrors the fixture in ``test_init_database_alembic.py``: ``get_settings``
    is ``@lru_cache``-d AND backed by a module global, so both must be reset
    for the env override to take effect.
    """
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_config, "_settings", None)
    app_config.get_settings.cache_clear()
    yield tmp_path
    app_config.get_settings.cache_clear()


def test_init_database_does_not_run_post_steps_when_blocked(
    isolated_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked upgrade state must short-circuit the post-migration steps.

    The 2026-06-02 squash collapsed the chain to a single baseline, so the
    historical "stamp behind / schema ahead → replay collides → blocked"
    setup can no longer be reproduced from the real script dir. We instead
    drive the contract directly: stub ``run_startup_migrations`` (as
    ``init_database`` imports it) to record a blocked ``ready=False`` state —
    exactly what a needs-confirmation gate or a failed apply would leave —
    and assert that the schema-drift post-step is never reached.
    """
    db_path = isolated_data_dir / "databases" / "default" / "app.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    def _blocked_startup(path: Path, **_kwargs: object) -> None:
        # Simulate the gate firing: the DB is left in a blocked state with a
        # real, operator-facing message instead of progressing to head.
        set_upgrade_state(
            path,
            ready=False,
            blocked_on=["9999_needs_confirmation"],
            last_backup=None,
            message="A migration needs confirmation before the app can finish upgrading.",
        )

    import chaoscypher_core.database.migrations.startup as startup_mod

    monkeypatch.setattr(startup_mod, "run_startup_migrations", _blocked_startup)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("post-migration step ran on a blocked DB")

    # The schema-drift gate assumes an at-head schema; it must be skipped
    # while the DB is blocked rather than crashing the boot.
    monkeypatch.setattr(drift_mod, "check_schema_drift", explode)

    # Must not raise — a blocked upgrade degrades to maintenance, it does
    # not crash the boot.
    init_database("default")

    assert get_upgrade_state(db_path).ready is False

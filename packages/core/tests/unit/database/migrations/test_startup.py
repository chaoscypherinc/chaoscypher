# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the tier-aware startup runner.

The tier-routing / partial-apply / gating tests below need a *multi-step*
migration chain with mixed tiers (safe_auto → needs_confirmation → manual)
to exercise. After the 2026-06-02 squash the production script directory
holds a single baseline (``0001``), so those scenarios are no longer
reproducible against the real migrations. :func:`_synthetic_chain` builds a
throwaway 3-revision chain in a temp script directory and points the runner
at it via ``_config_path``, keeping the routing/gating coverage independent
of the production migration count.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from chaoscypher_core.database.migrations.runner import (
    current_revision,
    head_revision,
)
from chaoscypher_core.database.migrations.startup import run_startup_migrations
from chaoscypher_core.database.migrations.state import get_upgrade_state


# --- synthetic migration chain harness -------------------------------------

_SYNTH_ENV_PY = '''\
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Minimal Alembic env for the synthetic test chain.

Reuses the caller-injected connection (online mode) exactly like the real
env.py, but needs no target_metadata: the synthetic migrations create their
own throwaway tables via explicit ``op.create_table`` rather than diffing
against SQLModel.metadata.
"""
from __future__ import annotations

from alembic import context


def run() -> None:
    connection = context.config.attributes.get("connection")
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        context.configure(connection=connection, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


run()
'''

_SYNTH_ALEMBIC_INI = """\
[alembic]
script_location = %(here)s
sqlalchemy.url =
file_template = %%(rev)s_%%(slug)s
"""


def _migration_module(
    *, revision: str, down_revision: str | None, tier: str, table: str
) -> str:
    down = repr(down_revision)
    return textwrap.dedent(
        f'''\
        # Copyright (C) 2024-2026 Chaos Cypher, Inc.
        # SPDX-License-Identifier: AGPL-3.0-only
        """synthetic {revision}"""
        from __future__ import annotations

        import sqlalchemy as sa
        from alembic import op

        revision = {revision!r}
        down_revision = {down}
        branch_labels = None
        depends_on = None

        CC_TIER = {tier!r}
        CC_DESCRIPTION = "synthetic {tier} migration {revision}"


        def upgrade() -> None:
            op.create_table(
                {table!r},
                sa.Column("id", sa.Integer(), primary_key=True),
            )


        def downgrade() -> None:
            op.drop_table({table!r})
        '''
    )


def _synthetic_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp 0001→0002→0003 chain and point the runner at it.

    Tiers: 0001 safe_auto (baseline), 0002 needs_confirmation, 0003 manual.
    Returns the path to the temp ``alembic.ini`` (also installed as the
    runner's ``_config_path``).
    """
    script_dir = tmp_path / "synth_migrations"
    versions = script_dir / "versions"
    versions.mkdir(parents=True)
    (script_dir / "env.py").write_text(_SYNTH_ENV_PY, encoding="utf-8")
    (script_dir / "script.py.mako").write_text("", encoding="utf-8")
    ini_path = script_dir / "alembic.ini"
    ini_path.write_text(_SYNTH_ALEMBIC_INI, encoding="utf-8")

    chain = [
        ("0001", None, "safe_auto", "synth_baseline"),
        ("0002", "0001", "needs_confirmation", "synth_nc"),
        ("0003", "0002", "manual", "synth_manual"),
    ]
    for revision, down, tier, table in chain:
        (versions / f"{revision}_synthetic.py").write_text(
            _migration_module(
                revision=revision, down_revision=down, tier=tier, table=table
            ),
            encoding="utf-8",
        )

    import chaoscypher_core.database.migrations.runner as runner_mod

    monkeypatch.setattr(runner_mod, "_config_path", lambda: ini_path)
    return ini_path


def test_fresh_db_runs_baseline_and_is_ready(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    run_startup_migrations(db)

    assert current_revision(db) == head_revision()
    state = get_upgrade_state(db)
    assert state.ready is True
    assert state.blocked_on == []


def test_upgrade_state_probe_before_init_keeps_fresh_classification(
    tmp_path: Path,
) -> None:
    """A pre-init get_upgrade_state() probe must not poison fresh installs.

    The CLI upgrade guard probes upgrade state BEFORE the engine ever
    initializes the database; get_upgrade_state() creates its
    chaoscypher_upgrade_state bookkeeping table as a side effect. If the
    fresh-install classifier counts that bookkeeping as a "user table",
    the DB is treated as pre-Alembic and STAMPED at the baseline instead
    of running it — with the squashed single-revision chain that equals
    "fully migrated", so no schema is ever created.
    """
    db = tmp_path / "app.db"
    get_upgrade_state(db)  # guard-style probe on a never-initialized DB

    run_startup_migrations(db)

    assert current_revision(db) == head_revision()
    from chaoscypher_core.adapters.sqlite.engine import get_engine

    with get_engine(db).connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name='graph_templates'"
        ).fetchall()
    assert rows, "baseline migration must have created the core schema"


def test_no_pending_is_noop(tmp_path: Path) -> None:
    db = tmp_path / "app.db"
    run_startup_migrations(db)
    # Second invocation: no pending — stay ready, no backup written
    # (run_startup_migrations only backs up when there's something pending).
    before = get_upgrade_state(db)
    run_startup_migrations(db)
    after = get_upgrade_state(db)
    assert before == after
    assert after.ready is True
    assert after.last_backup is None


# A full tier-2 routing test requires a real tier-2 migration in the
# script dir. Phase 7 (which ships the seven drift-closure migrations,
# two of them tier=needs_confirmation) provides that coverage end-to-end
# via test_drift_closure.py.


def test_plan_apply_all_safe() -> None:
    from chaoscypher_core.database.migrations.startup import _plan_apply
    from chaoscypher_core.database.migrations.tiers import MigrationInfo, MigrationTier

    infos = [
        MigrationInfo("0002", MigrationTier.SAFE_AUTO, ""),
        MigrationInfo("0003", MigrationTier.NEEDS_CONFIRMATION, ""),
    ]
    assert _plan_apply(infos, auto_apply_destructive=False) == (["0002", "0003"], [])


def test_plan_apply_destructive_on_takes_everything() -> None:
    from chaoscypher_core.database.migrations.startup import _plan_apply
    from chaoscypher_core.database.migrations.tiers import MigrationInfo, MigrationTier

    infos = [
        MigrationInfo("0002", MigrationTier.SAFE_AUTO, ""),
        MigrationInfo("0042", MigrationTier.MANUAL, ""),
        MigrationInfo("0044", MigrationTier.SAFE_AUTO, ""),
    ]
    assert _plan_apply(infos, auto_apply_destructive=True) == (
        ["0002", "0042", "0044"],
        [],
    )


def test_plan_apply_off_stops_before_first_manual() -> None:
    from chaoscypher_core.database.migrations.startup import _plan_apply
    from chaoscypher_core.database.migrations.tiers import MigrationInfo, MigrationTier

    infos = [
        MigrationInfo("0002", MigrationTier.SAFE_AUTO, ""),
        MigrationInfo("0041", MigrationTier.NEEDS_CONFIRMATION, ""),
        MigrationInfo("0042", MigrationTier.MANUAL, ""),
        MigrationInfo("0044", MigrationTier.SAFE_AUTO, ""),
    ]
    assert _plan_apply(infos, auto_apply_destructive=False) == (
        ["0002", "0041"],
        ["0042", "0044"],
    )


def test_plan_apply_off_first_is_manual_applies_nothing() -> None:
    from chaoscypher_core.database.migrations.startup import _plan_apply
    from chaoscypher_core.database.migrations.tiers import MigrationInfo, MigrationTier

    infos = [MigrationInfo("0042", MigrationTier.MANUAL, "")]
    assert _plan_apply(infos, auto_apply_destructive=False) == ([], ["0042"])


def _seed_baseline(tmp_path) -> Path:
    """Build a non-fresh DB stamped at the synthetic baseline 0001.

    Stamps (does not apply) so 0002/0003 of the synthetic chain remain
    genuinely pending — the runner sees a non-fresh DB with real work left,
    exactly the shape these routing tests need.
    """
    from chaoscypher_core.database.migrations.runner import (
        _make_config,
        head_revision,
    )

    # Sanity: the synthetic chain must be installed before this is called.
    assert head_revision() == "0003"

    from alembic import command

    from chaoscypher_core.adapters.sqlite.engine import get_engine

    db = tmp_path / "app.db"
    # Materialize the baseline's table so the DB is non-fresh, then stamp at
    # 0001 so 0002/0003 are still pending.
    engine = get_engine(db)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE synth_baseline (id INTEGER PRIMARY KEY)")
    cfg = _make_config(db)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.stamp(cfg, "0001")
    return db


def _first_manual(db) -> str | None:
    from chaoscypher_core.database.migrations.runner import pending_revisions
    from chaoscypher_core.database.migrations.tiers import (
        MigrationTier,
        read_migration_info,
    )

    for rev in pending_revisions(db):
        if read_migration_info(rev).tier is MigrationTier.MANUAL:
            return rev
    return None


def test_destructive_on_upgrades_to_head_and_records(tmp_path, monkeypatch) -> None:
    from chaoscypher_core.database.migrations.runner import (
        current_revision,
        head_revision,
        pending_revisions,
    )

    _synthetic_chain(tmp_path, monkeypatch)
    db = _seed_baseline(tmp_path)
    assert pending_revisions(db)  # there is real pending work (0002, 0003)

    run_startup_migrations(db, auto_apply_destructive=True)

    assert current_revision(db) == head_revision()
    state = get_upgrade_state(db)
    assert state.ready is True
    assert state.blocked_on == []
    assert state.data_changing is True  # baseline→head crosses tier-2 migrations
    assert state.last_backup is not None


def test_destructive_off_stops_before_first_manual(tmp_path, monkeypatch) -> None:
    from chaoscypher_core.database.migrations.runner import (
        current_revision,
        head_revision,
    )

    _synthetic_chain(tmp_path, monkeypatch)
    db = _seed_baseline(tmp_path)
    first_manual = _first_manual(db)
    assert first_manual == "0003"  # computed from the synthetic chain, not hardcoded

    run_startup_migrations(db, auto_apply_destructive=False)

    state = get_upgrade_state(db)
    assert state.ready is False
    assert state.blocked_on[0] == first_manual
    assert current_revision(db) != head_revision()
    assert state.last_backup is not None


def test_blocked_when_backup_space_insufficient(tmp_path, monkeypatch) -> None:
    import chaoscypher_core.database.migrations.startup as startup_mod

    _synthetic_chain(tmp_path, monkeypatch)
    db = _seed_baseline(tmp_path)
    monkeypatch.setattr(startup_mod, "free_space_ok", lambda *a, **k: False)

    run_startup_migrations(db, auto_apply_destructive=True)

    state = get_upgrade_state(db)
    assert state.ready is False
    assert "disk" in state.message.lower() or "space" in state.message.lower()


def _seed_schema_ahead_of_stamp(tmp_path) -> Path:
    """Build a DB whose physical schema is AHEAD of its recorded stamp.

    Mirrors the real production failure mode: an interrupted/partial
    upgrade (container killed mid-migration, power loss) — or a legacy DB
    from the retired reflective auto-migrator — leaves the schema at HEAD
    while ``alembic_version`` still points at an earlier revision.
    Replaying the now-"pending" migrations then throws ``duplicate
    column`` / ``table already exists`` because the objects already exist.

    Uses the synthetic chain: upgrade to head (all three synth tables
    created, stamped at 0003) then rewind the stamp to 0001 so replaying
    0002's ``create_table`` collides with the already-present table.
    """
    from sqlalchemy import text

    from chaoscypher_core.adapters.sqlite.engine import get_engine
    from chaoscypher_core.database.migrations.runner import upgrade_to_head

    db = tmp_path / "app.db"
    upgrade_to_head(db)  # full synthetic schema, stamped at HEAD (0003)
    engine = get_engine(db)
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = '0001'"))
    return db


def test_apply_failure_records_honest_state_without_raising(tmp_path, monkeypatch) -> None:
    """A migration that fails to apply must gate, not crash.

    When the schema is ahead of its stamp, replay raises an
    ``OperationalError``. The runner must catch it, record an honest
    ``ready=False`` state (so every surface degrades to maintenance mode
    instead of hard-crashing the boot before serving), and NOT propagate.
    """
    _synthetic_chain(tmp_path, monkeypatch)
    db = _seed_schema_ahead_of_stamp(tmp_path)

    # Must not raise — a blocked upgrade is an operational state, not a crash.
    run_startup_migrations(db, auto_apply_destructive=True)

    state = get_upgrade_state(db)
    assert state.ready is False
    assert state.blocked_on  # the migrations it could not finish
    assert state.message  # a real, non-empty explanation
    assert state.last_backup is not None  # pre-upgrade backup retained


def test_apply_failure_gating_survives_logging_error(tmp_path, monkeypatch) -> None:
    """Gating must not be undone by a logging failure.

    Recording the failure logs the traceback, which can itself raise on a
    console whose encoding can't render it (a real cp1252 Windows case).
    The gate's whole job is to NOT crash the boot, so a logging error must
    not propagate — the honest state still gets recorded.
    """
    import chaoscypher_core.database.migrations.startup as startup_mod

    _synthetic_chain(tmp_path, monkeypatch)
    db = _seed_schema_ahead_of_stamp(tmp_path)

    class _BoomLogger:
        def error(self, *_a, **_k):
            raise RuntimeError("console encoding boom")

        def debug(self, *_a, **_k):
            raise RuntimeError("console encoding boom")

        def info(self, *_a, **_k):
            pass

        def warning(self, *_a, **_k):
            pass

    monkeypatch.setattr(startup_mod, "logger", _BoomLogger())

    # Must not raise even though logging blows up mid-record.
    run_startup_migrations(db, auto_apply_destructive=True)

    state = get_upgrade_state(db)
    assert state.ready is False
    assert state.message  # honest state recorded despite the logging failure


def test_concurrent_starts_apply_once(tmp_path, monkeypatch) -> None:
    """Two threads racing run_startup_migrations -> DB reaches head, no error.

    msvcrt/fcntl locks are process-scoped, so two threads in one process
    don't truly contend on the file lock; this asserts the *idempotent
    outcome* (both finish, DB at head, no error), which is the real
    invariant. True multi-process contention is covered by the
    test_commit_concurrent_writer_lock.py pattern.
    """
    import threading

    from chaoscypher_core.database.migrations.runner import (
        current_revision,
        head_revision,
    )

    _synthetic_chain(tmp_path, monkeypatch)
    db = _seed_baseline(tmp_path)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            run_startup_migrations(db, auto_apply_destructive=True)
        except BaseException as exc:  # recorded for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert current_revision(db) == head_revision()
    assert get_upgrade_state(db).ready is True

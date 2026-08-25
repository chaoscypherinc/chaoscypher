# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the Alembic-backed migration runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chaoscypher_core.database.migrations.runner import (
    current_revision,
    downgrade_to,
    head_revision,
    pending_revisions,
    upgrade_to,
    upgrade_to_head,
)
from chaoscypher_core.exceptions import UnsupportedDatabaseLineageError


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "app.db"
    conn = sqlite3.connect(str(db_path))
    conn.close()
    return db_path


def test_head_revision_reads_latest_from_script_dir() -> None:
    head = head_revision()
    # Head grows as migrations land. Revisions may be plain 4-digit strings
    # ("0029") or descriptive ("0030_llm_stage_progress"). Assert only that
    # one exists, has a 4-digit integer-parseable leading prefix, and is >= 1
    # (i.e. at least the baseline migration exists).
    assert head is not None
    assert head[:4].isdigit() and int(head[:4]) >= 1, (
        f"head revision {head!r} should have a 4-digit integer-parseable "
        f"leading prefix >= 1 (i.e. at least the baseline migration exists)."
    )


def test_current_revision_is_none_on_empty_db(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    assert current_revision(db) is None


def test_pending_revisions_after_fresh_db_includes_baseline(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    pending = pending_revisions(db)
    assert len(pending) >= 1
    assert any(rev.startswith("0001") for rev in pending)


def test_upgrade_to_head_applies_baseline(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    upgrade_to_head(db)

    assert current_revision(db) == head_revision()
    assert pending_revisions(db) == []


def test_second_upgrade_is_noop(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    upgrade_to_head(db)
    upgrade_to_head(db)  # Must not raise.
    assert pending_revisions(db) == []


# ----- ensure_stamped -------------------------------------------------------

from sqlmodel import SQLModel  # noqa: E402

from chaoscypher_core.adapters.sqlite.engine import get_engine  # noqa: E402
from chaoscypher_core.database.migrations.runner import ensure_stamped  # noqa: E402


def test_ensure_stamped_noop_on_fresh_db(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    ensure_stamped(db)
    # Fresh DB has no user tables — ensure_stamped leaves it alone so
    # the real upgrade path can CREATE them.
    assert current_revision(db) is None


def test_ensure_stamped_marks_preexisting_schema_at_baseline(tmp_path: Path) -> None:
    # Simulate a pre-Alembic install: build the schema via create_all,
    # then verify ensure_stamped marks it at the baseline (0001) — NOT
    # head — so subsequent Alembic migrations in the chain still run.
    # Stamping at head would silently skip constraint-change migrations
    # that must apply to existing DBs.
    db_path = tmp_path / "app.db"
    engine = get_engine(db_path)
    SQLModel.metadata.create_all(engine)

    ensure_stamped(db_path)
    assert current_revision(db_path) == "0001"


def test_ensure_stamped_noop_on_already_stamped(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    upgrade_to_head(db)
    rev_before = current_revision(db)
    ensure_stamped(db)  # Must not re-stamp or raise.
    assert current_revision(db) == rev_before


# ----- upgrade_to / downgrade_to -------------------------------------------


def test_upgrade_to_baseline_only_applies_baseline(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    upgrade_to(db, "0001")
    assert current_revision(db) == "0001"


def test_upgrade_to_head_via_revision_matches_head(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    head = head_revision()
    assert head is not None
    upgrade_to(db, head)
    assert current_revision(db) == head


# The 0001 baseline's ``downgrade()`` is an intentional guarded no-op — the
# baseline is the schema floor; rolling below it would wipe the DB, so
# production rollback uses a backup restore instead (see the baseline's
# downgrade docstring). The two tests below pin that floor behaviour.


def test_downgrade_baseline_to_base_is_guarded_noop(tmp_path: Path) -> None:
    """Downgrading head to ``base`` runs the no-ops and leaves the schema.

    The baseline ``downgrade()`` is a deliberate ``pass`` (it does NOT drop
    tables), so a downgrade-to-base unstamps the version row without
    destroying user tables. This guards against a future edit that turns the
    baseline downgrade into a destructive ``drop_table`` sweep.
    """
    db = _fresh_db(tmp_path)
    upgrade_to_head(db)
    assert current_revision(db) == head_revision()

    downgrade_to(db, "base")  # must not raise

    # Version row cleared, but the baseline's tables survive the no-op.
    assert current_revision(db) is None
    engine = get_engine(db)
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'alembic_%'"
        ).fetchall()
    assert rows, "baseline downgrade must not drop user tables"


def test_ensure_stamped_refuses_unknown_revision(tmp_path: Path) -> None:
    """An alembic_version row we don't ship is refused, never silently re-stamped.

    Covers the 'old Alembic setup was deleted but left a stamp behind' case.
    Re-stamping such a database at the baseline (the pre-2026-08-14 behaviour)
    replayed 0002→HEAD against a schema those migrations were never written
    for, so the boot died later in the drift gate with an opaque
    ``SchemaIntegrityError``. Refusing here keeps the failure honest.
    """
    db = _fresh_db(tmp_path)
    engine = get_engine(db)
    SQLModel.metadata.create_all(engine)  # Real schema in place.

    # Write an orphan revision into alembic_version — this revision does
    # not exist in our script directory.
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql("DELETE FROM alembic_version")
        conn.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('999_ghost_revision')"
        )

    assert current_revision(db) == "999_ghost_revision"

    with pytest.raises(UnsupportedDatabaseLineageError) as excinfo:
        ensure_stamped(db)

    assert excinfo.value.revision == "999_ghost_revision"
    assert excinfo.value.code == "UNSUPPORTED_DATABASE_LINEAGE"
    # The stamp is left exactly as found — no silent re-stamp.
    assert current_revision(db) == "999_ghost_revision"


def test_unsupported_lineage_message_is_guided(tmp_path: Path) -> None:
    """The refusal message names the revision and the operator's way out.

    A bare "unsupported revision" would leave an operator with nowhere to go.
    The message must carry (a) the offending revision id, (b) both causes an
    unresolvable revision can have — the retired pre-2026-06-02 / pre-v0.1.0
    lineage, *or* a build newer than this one — and (c) the recovery for each.

    The downgrade branch is load-bearing. ``ensure_stamped`` cannot tell the
    two apart from a revision id, and a database stamped *ahead* of the code
    is healthy: sending that operator down the "re-create or export/re-import"
    path would destroy live data when the real fix is to put the newer build
    back. So the non-destructive advice must be present and must come first.
    """
    db = _fresh_db(tmp_path)
    engine = get_engine(db)
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"
        )
        conn.exec_driver_sql("DELETE FROM alembic_version")
        conn.exec_driver_sql("INSERT INTO alembic_version (version_num) VALUES ('0044')")

    with pytest.raises(UnsupportedDatabaseLineageError) as excinfo:
        ensure_stamped(db)

    message = excinfo.value.message
    lowered = message.lower()
    assert "0044" in message
    assert "2026-06-02" in message
    assert "v0.1.0" in message
    # Both causes named, and the destructive advice is conditional ("otherwise").
    assert "newer build" in lowered
    for guidance in ("downgraded", "re-install", "otherwise", "back up", "re-create", "export"):
        assert guidance in lowered, f"missing guidance {guidance!r}: {message}"
    # Non-destructive advice first — an operator who stops reading early must
    # not act on the destructive branch.
    assert lowered.index("re-install") < lowered.index("re-create"), (
        "the downgrade fix must precede the re-create/export advice"
    )
    assert excinfo.value.details["revision"] == "0044"


def test_ensure_stamped_refuses_pre_squash_revision(tmp_path: Path) -> None:
    """A DB stamped at a now-deleted pre-squash revision refuses startup.

    The 2026-06-02 squash collapsed migrations 0001-0050 into a single
    0001 baseline, so any existing database recorded at a revision like
    ``0050_chunk_job_finalize_claimed`` points at a script the package no
    longer ships. Ruled unsupported (2026-08-14): the squash predates every
    public release, so no released build ever produced such a database, and
    re-stamping one at the baseline only defers the failure to the drift
    gate. ``ensure_stamped`` refuses it up front instead.
    """
    db = tmp_path / "legacy.db"
    # Build the current schema via the real startup path, then forge a
    # pre-squash alembic_version row.
    from chaoscypher_core.database.migrations.startup import run_startup_migrations

    run_startup_migrations(db)

    import sqlite3

    con = sqlite3.connect(str(db))
    con.execute(
        "UPDATE alembic_version SET version_num = '0050_chunk_job_finalize_claimed'"
    )
    con.commit()
    con.close()

    assert current_revision(db) == "0050_chunk_job_finalize_claimed"

    with pytest.raises(UnsupportedDatabaseLineageError):
        ensure_stamped(db)

    assert current_revision(db) == "0050_chunk_job_finalize_claimed"


def test_startup_migrations_propagate_unsupported_lineage(tmp_path: Path) -> None:
    """The startup runner surfaces the refusal instead of upgrading anyway.

    ``run_startup_migrations`` is what every entry point calls, so the
    refusal has to escape it — its internal apply-failure gate must not
    swallow this into a maintenance-mode state that offers an Apply button
    for a database that can never be applied.
    """
    from chaoscypher_core.database.migrations.startup import run_startup_migrations

    db = tmp_path / "legacy.db"
    run_startup_migrations(db)

    import sqlite3

    con = sqlite3.connect(str(db))
    con.execute("UPDATE alembic_version SET version_num = '0044'")
    con.commit()
    con.close()

    with pytest.raises(UnsupportedDatabaseLineageError):
        run_startup_migrations(db)

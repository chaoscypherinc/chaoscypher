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


# After the 2026-06-02 squash there is exactly one revision (the 0001
# baseline), so there is no intermediate downgrade target left to walk to.
# The baseline's ``downgrade()`` is an intentional guarded no-op — the
# baseline is the schema floor; rolling below it would wipe the DB, so
# production rollback uses a backup restore instead (see the baseline's
# downgrade docstring). The two tests below pin that floor behaviour.


def test_downgrade_baseline_to_base_is_guarded_noop(tmp_path: Path) -> None:
    """Downgrading the baseline to ``base`` runs its no-op and leaves the schema.

    The baseline ``downgrade()`` is a deliberate ``pass`` (it does NOT drop
    tables), so a downgrade-to-base unstamps the version row without
    destroying user tables. This guards against a future edit that turns the
    baseline downgrade into a destructive ``drop_table`` sweep.
    """
    db = _fresh_db(tmp_path)
    upgrade_to_head(db)
    assert current_revision(db) == "0001"

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


def test_ensure_stamped_recovers_orphan_revision(tmp_path: Path) -> None:
    """An alembic_version row pointing at a revision we don't ship gets re-stamped.

    Covers the 'old Alembic setup was deleted but left a stamp behind' case.
    Without this recovery, upgrade_to_head would crash with
    'Can't locate revision identified by ...'.
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

    ensure_stamped(db)

    # After recovery, the row is re-stamped at the baseline (0001).
    # Post-baseline migrations will be re-applied on the next boot.
    assert current_revision(db) == "0001"


def test_ensure_stamped_recovers_squashed_away_revision(tmp_path: Path) -> None:
    """A DB stamped at a now-deleted pre-squash revision is re-stamped to baseline.

    The 2026-06-02 squash collapsed migrations 0001-0050 into a single
    0001 baseline, so any existing database recorded at a revision like
    ``0050_chunk_job_finalize_claimed`` now points at a script the package
    no longer ships. ``ensure_stamped`` must re-stamp it to the baseline
    (the schema is unchanged, so no data is lost) instead of letting
    ``upgrade_to_head`` crash on an unresolvable revision.
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

    ensure_stamped(db)  # must not raise

    assert current_revision(db) == "0001"

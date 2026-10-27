# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Alembic runner with app-aware engine reuse.

Wraps Alembic's Python API with convenience functions that use the
chaoscypher-core engine (complete with WAL-mode PRAGMAs and sqlite-vec
loading) rather than spinning up a fresh Alembic engine. Every call
goes through the same cached engine the app uses, which matters for
extension loading and concurrency behaviour.

Exposes four primitives:

* :func:`head_revision` — latest revision declared in the script dir
* :func:`current_revision` — revision the live DB is stamped at (None if empty)
* :func:`pending_revisions` — list of revisions between current and head
* :func:`upgrade_to_head` — apply every pending revision in order

Higher-level orchestration (see
:mod:`chaoscypher_core.database.migrations.upgrade`) builds on these
primitives to add backup and tier-aware routing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from importlib import resources
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import UnsupportedDatabaseLineageError


logger = structlog.get_logger(__name__)


def _config_path() -> Path:
    """Return the path to the shipped alembic.ini inside the package."""
    pkg = resources.files("chaoscypher_core.database.migrations")
    return Path(str(pkg / "alembic.ini"))


def _make_config(db_path: Path) -> Config:
    """Build an Alembic Config bound to a SQLite DB path.

    The script_location is relative to the on-disk ini, so we don't need
    to override it. sqlalchemy.url is set explicitly so offline-mode
    operations (which Alembic uses for autogenerate and some checks)
    have the right URL.
    """
    cfg = Config(str(_config_path()))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


@contextmanager
def _migration_connection(db_path: Path) -> Iterator[Connection]:
    """A connection of the app's engine for one Alembic command, in one transaction.

    ``env.py`` turns foreign keys off for the batch rebuilds and back on at the end, but
    ``PRAGMA foreign_keys`` is a no-op inside a transaction: once a migration has written a
    row, that turn back on did nothing, and the connection went back to the pool with foreign
    keys off. The next session on it could write a row naming a template that does not exist.
    They are turned on here, after the transaction has ended, so every pooled connection
    enforces them.
    """
    engine = get_engine(db_path)
    with engine.connect() as connection:
        try:
            with connection.begin():
                yield connection
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def head_revision() -> str | None:
    """Return the latest revision id declared in the scripts directory."""
    cfg = Config(str(_config_path()))
    script = ScriptDirectory.from_config(cfg)
    return script.get_current_head()


def current_revision(db_path: Path) -> str | None:
    """Return the revision id stamped on the live DB, or None if empty."""
    engine = get_engine(db_path)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()


def pending_revisions(db_path: Path) -> list[str]:
    """Return revision ids between current and head, in apply order."""
    cfg = Config(str(_config_path()))
    script = ScriptDirectory.from_config(cfg)
    current = current_revision(db_path)
    head = script.get_current_head()
    if head is None:
        return []
    # walk_revisions returns ordered head-to-base; reverse for apply order.
    revs = [r.revision for r in script.walk_revisions(base=current or "base", head=head)]
    revs.reverse()
    # walk_revisions includes the ``current`` endpoint; drop it so only
    # genuinely-pending revisions are returned.
    if current is not None and revs and revs[0] == current:
        revs = revs[1:]
    return revs


def upgrade_to_head(db_path: Path) -> None:
    """Apply every pending revision up to head, reusing the app's engine.

    Shares a connection with the app's cached engine so extension loading
    (sqlite-vec) and PRAGMAs are already in place. Wraps the whole upgrade
    in a single transaction via Alembic's begin_transaction().
    """
    upgrade_to(db_path, "head")


def upgrade_to(db_path: Path, revision: str) -> None:
    """Upgrade the DB to a specific Alembic revision (or ``"head"``).

    Shares a connection with the app's cached engine so extension loading
    (sqlite-vec) and PRAGMAs are already in place. Wraps the whole upgrade
    in a single transaction via Alembic's begin_transaction().
    """
    cfg = _make_config(db_path)
    with _migration_connection(db_path) as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, revision)
    logger.info("alembic_upgrade_complete", db_path=str(db_path), revision=revision)


def downgrade_to(db_path: Path, revision: str) -> None:
    """Downgrade the DB to a specific Alembic revision (or ``"base"``).

    Wraps Alembic's downgrade in the same shared-engine transaction
    pattern as :func:`upgrade_to`. Tests roundtrip reversibility; not yet
    wired to a user-facing CLI command.
    """
    cfg = _make_config(db_path)
    with _migration_connection(db_path) as connection:
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, revision)
    logger.info("alembic_downgrade_complete", db_path=str(db_path), revision=revision)


def _schema_has_non_alembic_tables(db_path: Path) -> bool:
    """True if the DB has user tables beyond Alembic/ChaosCypher bookkeeping.

    ``chaoscypher_%`` bookkeeping (e.g. ``chaoscypher_upgrade_state``, which
    ``get_upgrade_state()`` creates as a side effect of a pre-init probe such
    as the CLI upgrade guard) must NOT count as user tables: counting it
    makes a virgin DB classify as a pre-Alembic install, which gets STAMPED
    at the baseline instead of running it — skipping schema creation
    entirely on a squashed single-revision chain.
    """
    engine = get_engine(db_path)
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'alembic_%' "
            "AND name NOT LIKE 'chaoscypher_%'"
        ).fetchall()
    return bool(rows)


class SchemaStart(StrEnum):
    """What a database's schema starts from: the one decision every build or upgrade makes.

    The startup run (every open: Cortex, the worker, the Engine, the CLI,
    ``chaoscypher mcp``, a new or composed database) and the explicit apply
    (``chaoscypher upgrade``, ``db migrate apply``, the Maintenance page, MCP
    ``apply_upgrade``) both ask it, so a database is never an upgrade on one
    surface and a first build on another.
    """

    NEW = "new"
    """No version row and no tables: build it from nothing, quietly."""

    UNFINISHED = "unfinished"
    """No version row; tables, none of the migrations' tables holding a row.

    A first build that did not finish: SQLite commits the baseline's ``CREATE TABLE``
    statements as they run, while the version row is written only when the whole run
    commits. Or a search index opened before the schema was built. Nothing in it is
    data, so it is built again from nothing, quietly.
    """

    UNVERSIONED = "unversioned"
    """No version row; a table the migrations make holds rows: a pre-Alembic install.

    Stamped at the baseline and upgraded like any old database, after a backup.
    """

    VERSIONED = "versioned"
    """A version row: built before, so anything pending is an upgrade."""


def _migration_tables(conn: Connection) -> list[str]:
    """The tables in the database that the migrations make, ``alembic_version`` included.

    The migrations build exactly the models' schema (pinned by the autogenerate-diff
    test); the search index's tables, ``chaoscypher_*`` bookkeeping and SQLite's own are
    made elsewhere and are not among them.
    """
    from sqlmodel import SQLModel

    from chaoscypher_core.adapters.sqlite import models as _models  # noqa: F401 — registers them

    owned = {*SQLModel.metadata.tables, "alembic_version"}
    rows = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
    return sorted(row[0] for row in rows if row[0] in owned)


def _quoted(table: str) -> str:
    return '"' + table.replace('"', '""') + '"'


def _holds_rows(conn: Connection, tables: list[str]) -> bool:
    """Whether any of ``tables`` (names from the models' metadata) holds a row."""
    for table in tables:
        sql = f"SELECT 1 FROM {_quoted(table)} LIMIT 1"  # noqa: S608 — names from metadata
        if conn.exec_driver_sql(sql).first() is not None:
            return True
    return False


def schema_start(db_path: Path) -> SchemaStart:
    """Classify the database at ``db_path`` (see :class:`SchemaStart`); read-only."""
    if current_revision(db_path) is not None:
        return SchemaStart.VERSIONED
    if not _schema_has_non_alembic_tables(db_path):
        return SchemaStart.NEW
    with get_engine(db_path).connect() as conn:
        if _holds_rows(conn, _migration_tables(conn)):
            return SchemaStart.UNVERSIONED
    return SchemaStart.UNFINISHED


def drop_unfinished_schema(db_path: Path) -> bool:
    """Drop the migrations' tables of an :attr:`SchemaStart.UNFINISHED` database.

    The check and the drops are one write transaction (``BEGIN IMMEDIATE``) on one
    connection: a writer that commits a row after the caller classed the database
    is either seen by the check or waits for the drops. Every table dropped is empty,
    so nothing is lost; the baseline can then create them again. Tables made
    elsewhere (the search index, bookkeeping) are left. Interrupted, it leaves a
    database that is still unfinished.

    Returns:
        False, dropping nothing, when the database has a version row or one of the
        migrations' tables holds a row by the time the write lock is taken: it is
        not unfinished (any more), and is upgraded as an install instead.
    """
    with get_engine(db_path).connect() as conn:
        # Off before the transaction: inside one the pragma does nothing.
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                tables = _migration_tables(conn)
                versioned = "alembic_version" in tables and _holds_rows(conn, ["alembic_version"])
                if versioned or _holds_rows(conn, tables):
                    conn.rollback()
                    logger.warning(
                        "alembic_unfinished_schema_kept",
                        db_path=str(db_path),
                        reason="a_row_or_version_arrived",
                    )
                    return False
                for table in tables:
                    conn.exec_driver_sql(f"DROP TABLE {_quoted(table)}")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()
    if tables:
        logger.warning(
            "alembic_unfinished_schema_dropped",
            db_path=str(db_path),
            tables=len(tables),
            reason="first_build_did_not_finish",
        )
    return True


def _revision_is_known(revision: str) -> bool:
    """True if ``revision`` is resolvable in our script directory.

    Blind spot: resolution is by id, so a pre-squash database stamped at an
    old ``0002``-``0006`` collides with the post-squash chain's same-named
    revisions and is classified healthy rather than refused — inherent to
    bare-numeral revision ids. Exposure is dev-only: the squash predates
    v0.1.0, and the pre-squash chain reached ``0050``, so a real-world
    pre-squash database is overwhelmingly likely to sit outside that window.
    """
    cfg = Config(str(_config_path()))
    script = ScriptDirectory.from_config(cfg)
    try:
        return script.get_revision(revision) is not None
    except Exception:
        # ResolutionError / CommandError / anything else → treat as unknown.
        return False


_BASELINE_REVISION = "0001"


def ensure_stamped(db_path: Path) -> None:
    """Mark a DB as being at the baseline revision when needed.

    Handles three pre-Alembic-or-misaligned-stamp scenarios:

    1. **Pre-Alembic install** (:attr:`SchemaStart.UNVERSIONED`): user tables
       holding rows + no ``alembic_version`` row. Stamp at ``_BASELINE_REVISION``
       (not HEAD) so subsequent migrations in the chain still run on this DB. Running the baseline
       upgrade would otherwise fail on ``CREATE TABLE`` of already-existing
       tables, which is why we stamp instead of applying.
    2. **Stamp from a lineage we don't ship:** the ``alembic_version`` row
       names a revision our script dir can't resolve — in practice a
       database from the pre-2026-06-02 chain that the squash deleted.
       Raise :class:`UnsupportedDatabaseLineageError`. We deliberately do
       NOT re-stamp at the baseline: that only relabels the row, leaving a
       schema the shipped 0002+ migrations were never written for, so the
       boot dies further downstream in the drift gate with an opaque
       ``SchemaIntegrityError`` and restarts forever. Failing here, loudly
       and once, is what an operator can actually act on.
    3. **Healthy stamp:** revision is known to our script dir — no-op.

    No-op on new and unfinished DBs (no version row, no rows in the migrations'
    tables) so the startup run can build the schema from scratch.

    Stamping at the baseline (rather than HEAD) is load-bearing: with
    constraint-change migrations shipped after the baseline, stamping
    at HEAD would silently skip those migrations on pre-Alembic installs,
    leaving the DB with schema drift we'd otherwise have applied.

    Args:
        db_path: Path to the SQLite database file.

    Raises:
        UnsupportedDatabaseLineageError: The DB is stamped at a revision
            this build's script directory does not contain.
    """
    start = schema_start(db_path)
    if start is SchemaStart.VERSIONED:
        current = current_revision(db_path)
        if current is not None and not _revision_is_known(current):
            logger.error(
                "alembic_unsupported_database_lineage",
                db_path=str(db_path),
                unknown_revision=current,
            )
            raise UnsupportedDatabaseLineageError(current, db_path=str(db_path))
        return
    if start is not SchemaStart.UNVERSIONED:
        # New or unfinished: no data, nothing to stamp. The startup run builds it
        # from nothing; stamping an unfinished one would make its build an upgrade.
        return

    cfg = _make_config(db_path)
    with _migration_connection(db_path) as connection:
        cfg.attributes["connection"] = connection
        command.stamp(cfg, _BASELINE_REVISION)
    logger.info(
        "alembic_stamped_at_baseline",
        db_path=str(db_path),
        revision=_BASELINE_REVISION,
        reason="preexisting_schema",
    )


__all__ = [
    "SchemaStart",
    "current_revision",
    "downgrade_to",
    "drop_unfinished_schema",
    "ensure_stamped",
    "head_revision",
    "pending_revisions",
    "schema_start",
    "upgrade_to",
    "upgrade_to_head",
]

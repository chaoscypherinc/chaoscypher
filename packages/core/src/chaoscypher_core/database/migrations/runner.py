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

Phase 4 wraps these in a tier-aware runner that adds backup and routing.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from chaoscypher_core.adapters.sqlite.engine import get_engine


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
    engine = get_engine(db_path)
    cfg = _make_config(db_path)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, revision)
    logger.info("alembic_upgrade_complete", db_path=str(db_path), revision=revision)


def downgrade_to(db_path: Path, revision: str) -> None:
    """Downgrade the DB to a specific Alembic revision (or ``"base"``).

    Wraps Alembic's downgrade in the same shared-engine transaction
    pattern as :func:`upgrade_to`. Tests roundtrip reversibility; not yet
    wired to a user-facing CLI command.
    """
    engine = get_engine(db_path)
    cfg = _make_config(db_path)
    with engine.begin() as connection:
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


def _revision_is_known(revision: str) -> bool:
    """True if ``revision`` is resolvable in our script directory."""
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

    1. **Pre-Alembic install:** existing user tables + no ``alembic_version``
       row. Stamp at ``_BASELINE_REVISION`` (not HEAD) so subsequent
       migrations in the chain still run on this DB. Running the baseline
       upgrade would otherwise fail on ``CREATE TABLE`` of already-existing
       tables, which is why we stamp instead of applying.
    2. **Stale stamp from a deleted Alembic chain:** a previous Alembic
       setup (later removed) left an ``alembic_version`` row referencing a
       revision that our current script dir doesn't know about. Without
       this recovery, ``upgrade_to_head`` would crash with
       ``Can't locate revision identified by '<stale_id>'``. We detect the
       orphan and re-stamp at the baseline so 0002+ migrations still run.
    3. **Healthy stamp:** revision is known to our script dir — no-op.

    No-op on fresh DBs (no user tables, no version row) so the normal
    upgrade path can create the schema from scratch.

    Stamping at the baseline (rather than HEAD) is load-bearing: with
    constraint-change migrations shipped after the baseline, stamping
    at HEAD would silently skip those migrations on pre-Alembic installs,
    leaving the DB with schema drift we'd otherwise have applied.

    Args:
        db_path: Path to the SQLite database file.
    """
    current = current_revision(db_path)

    purge = False
    if current is None:
        if not _schema_has_non_alembic_tables(db_path):
            return
        reason = "preexisting_schema"
    elif not _revision_is_known(current):
        reason = "orphan_stamp"
        purge = True  # Clear the orphan row before re-stamping; stamp()
        # otherwise fails resolving the old→new path.
        logger.warning(
            "alembic_orphan_stamp_detected",
            db_path=str(db_path),
            orphan_revision=current,
        )
    else:
        return

    engine = get_engine(db_path)
    cfg = _make_config(db_path)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.stamp(cfg, _BASELINE_REVISION, purge=purge)
    logger.info(
        "alembic_stamped_at_baseline",
        db_path=str(db_path),
        revision=_BASELINE_REVISION,
        reason=reason,
    )


__all__ = [
    "current_revision",
    "downgrade_to",
    "ensure_stamped",
    "head_revision",
    "pending_revisions",
    "upgrade_to",
    "upgrade_to_head",
]

# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tier-aware self-healing migration runner invoked from init_database.

On startup we take a verified backup (gated by a free-disk preflight)
and then apply pending migrations under a cross-process lock, routing by
CC_TIER and by the ``auto_apply_destructive`` setting:

- ``auto_apply_destructive`` true (default): apply ALL pending migrations
  to head — SAFE_AUTO, NEEDS_CONFIRMATION, and MANUAL alike — so routine
  updates "just work" for MCP / web users without operator action. A
  data-changing upgrade is recorded in ``chaoscypher_upgrade_state``
  (``last_applied`` + ``data_changing``) so it can still be rolled back
  and surfaced afterwards.
- ``auto_apply_destructive`` false: apply SAFE_AUTO + NEEDS_CONFIRMATION
  up to — but not including — the first MANUAL migration, then write the
  blocked_on list so cortex + worker stay in maintenance mode until the
  operator clicks Apply in the UI (or runs ``chaoscypher db migrate
  apply``). If the backup can't be taken safely (disk), we block without
  applying anything.

Fresh installs bypass the gate entirely (straight to head, no backup —
there is no user data to protect).
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.database.backup import backup_database, free_space_ok
from chaoscypher_core.database.migrations.runner import (
    _schema_has_non_alembic_tables,
    current_revision,
    ensure_stamped,
    pending_revisions,
    upgrade_to,
    upgrade_to_head,
)
from chaoscypher_core.database.migrations.state import (
    clear_upgrade_state,
    describe_apply_failure,
    record_successful_upgrade,
    set_upgrade_state,
)
from chaoscypher_core.database.migrations.tiers import (
    MigrationInfo,
    MigrationTier,
    read_migration_info,
)
from chaoscypher_core.utils.filelock import lock_file, unlock_file


if TYPE_CHECKING:
    from pathlib import Path


logger = structlog.get_logger(__name__)


def _is_fresh_install(db_path: Path) -> bool:
    """True if the DB has no Alembic revision AND no user tables.

    Fresh install: no rows in alembic_version, no non-alembic tables.
    Safe to upgrade straight to head regardless of tier — constraint
    migrations operate on empty tables and can't lose data.
    """
    return current_revision(db_path) is None and not _schema_has_non_alembic_tables(
        db_path
    )


def _plan_apply(
    infos: list[MigrationInfo],
    *,
    auto_apply_destructive: bool,
) -> tuple[list[str], list[str]]:
    """Split ordered pending migrations into (to_apply, blocked).

    ``infos`` is in Alembic apply order. With ``auto_apply_destructive``
    True, everything is applied. Otherwise we apply safe/data-changing
    migrations up to — but not including — the first ``manual`` migration,
    and block on that one plus everything after it (linear history can't
    skip it).
    """
    if auto_apply_destructive:
        return [info.revision for info in infos], []

    to_apply: list[str] = []
    for i, info in enumerate(infos):
        if info.tier is MigrationTier.MANUAL:
            return to_apply, [later.revision for later in infos[i:]]
        to_apply.append(info.revision)
    return to_apply, []


def _resolve_auto_apply_destructive(value: bool | None) -> bool:
    """Resolve the auto-apply flag.

    The explicit ``value`` is authoritative — every Engine-driven caller
    passes ``settings.migrations.auto_apply_destructive``. When ``None``
    (entry points that pre-date settings threading — bootstrap/CLI/MCP), it
    falls back to the ``MigrationsSettings`` group default, which honours the
    ``CHAOSCYPHER_AUTO_APPLY_DESTRUCTIVE`` env override. This deliberately
    does NOT consult any settings singleton.
    """
    if value is not None:
        return value
    from chaoscypher_core.settings import MigrationsSettings

    return MigrationsSettings().auto_apply_destructive


def run_startup_migrations(
    db_path: Path,
    *,
    auto_apply_destructive: bool | None = None,
) -> None:
    """Run migrations at startup, routing by tier under a cross-process lock.

    Default (resolved to True from settings): apply ALL pending to head after
    a verified backup. When False: apply safe/data-changing migrations up to
    the first ``manual`` one, then block. If the backup can't be taken safely
    (disk), block without applying. Fresh installs skip the gate (straight to
    head, no backup — no data to protect).

    Args:
        db_path: Path to the SQLite database.
        auto_apply_destructive: Override the settings default (mainly tests).
    """
    apply_destructive = _resolve_auto_apply_destructive(auto_apply_destructive)
    fresh = _is_fresh_install(db_path)

    ensure_stamped(db_path)
    pending = pending_revisions(db_path)
    if not pending:
        clear_upgrade_state(db_path)
        return

    if fresh:
        logger.info("startup_migrations_fresh_install", pending=pending)
        upgrade_to_head(db_path)
        clear_upgrade_state(db_path)
        return

    lock_path = db_path.with_suffix(db_path.suffix + ".upgrade.lock")
    try:
        with open(lock_path, "w", encoding="utf-8") as lock_handle:
            try:
                lock_file(lock_handle, blocking=False)
            except BlockingIOError:
                logger.info("startup_migrations_waiting_for_lock", db=str(db_path))
                lock_file(lock_handle, blocking=True)
            try:
                # Re-read under the lock: another process may have applied
                # while we waited (or between the pre-lock read and
                # acquiring the lock).
                pending = pending_revisions(db_path)
                if not pending:
                    clear_upgrade_state(db_path)
                    return
                _run_locked(db_path, pending, apply_destructive=apply_destructive)
            finally:
                unlock_file(lock_handle)
    finally:
        # Best-effort cleanup, mirroring engine.py's .init.lock handling.
        # The lock is advisory (released via unlock_file + handle close), so
        # a leftover file is harmless — we just don't want to litter.
        with contextlib.suppress(OSError):
            lock_path.unlink(missing_ok=True)


def _record_apply_failure(
    db_path: Path,
    *,
    original_pending: list[str],
    backup_str: str | None,
    exc: Exception,
) -> None:
    """Record an honest blocked state after a startup migration fails to apply.

    A failed upgrade is an operational state, not a crash. Every surface
    (Cortex maintenance page, MCP maintenance server, ``chaoscypher db
    migrate status``) reads the upgrade-state row, so recording the real
    reason here — instead of letting the exception propagate and hard-crash
    the boot before a maintenance response can be served — is what keeps the
    failure visible and actionable.
    """
    try:
        remaining = pending_revisions(db_path) or original_pending
    except Exception:  # state read is best-effort
        remaining = original_pending

    # Record the honest state FIRST — this is the durable, must-happen action
    # that lets every surface degrade to maintenance instead of crashing.
    set_upgrade_state(
        db_path,
        ready=False,
        blocked_on=remaining,
        last_backup=backup_str,
        message=describe_apply_failure(exc, last_backup=backup_str),
    )

    # Logging is best-effort and MUST NOT turn a gated failure back into a
    # crash — e.g. a console whose encoding can't render the traceback (a
    # real cp1252 Windows case) would otherwise re-raise out of the gate.
    try:
        logger.error(
            "startup_migrations_apply_failed",
            blocked_on=remaining,
            backup=backup_str,
            error_type=type(exc).__name__,
        )
        logger.debug("startup_migrations_apply_failed_traceback", exc_info=exc)
    except Exception:  # never let logging crash the gate
        pass


def _run_locked(
    db_path: Path, pending: list[str], *, apply_destructive: bool
) -> None:
    infos: list[MigrationInfo] = [read_migration_info(rev) for rev in pending]
    to_apply, blocked = _plan_apply(infos, auto_apply_destructive=apply_destructive)

    # We always back up before touching data — gate on free space first.
    if not free_space_ok(db_path):
        logger.warning("startup_migrations_blocked_no_space", pending=pending)
        set_upgrade_state(
            db_path,
            ready=False,
            blocked_on=pending,
            last_backup=None,
            message=(
                "Not enough free disk space to back up the database before "
                "upgrading. Free up space and restart, or apply manually."
            ),
        )
        return

    # Back up, then apply. A failure here (e.g. the live schema is ahead of
    # its recorded stamp after an interrupted upgrade, so a migration re-adds
    # an existing column) must gate, not crash: record the real reason and
    # return so every surface degrades to maintenance mode.
    backup_str: str | None = None
    try:
        backup = backup_database(db_path, label=f"pre-{pending[0]}")
        backup_str = str(backup.backup_path)

        if to_apply:
            if to_apply == pending:
                upgrade_to_head(db_path)
            else:
                upgrade_to(db_path, to_apply[-1])
    except Exception as exc:  # any apply failure must gate, never crash the boot
        _record_apply_failure(
            db_path, original_pending=pending, backup_str=backup_str, exc=exc
        )
        return

    applied_infos = infos[: len(to_apply)]
    data_changing = any(
        info.tier is not MigrationTier.SAFE_AUTO for info in applied_infos
    )

    if blocked:
        logger.warning(
            "startup_migrations_partial_then_blocked",
            applied=to_apply,
            blocked_on=blocked,
            backup=backup_str,
        )
        set_upgrade_state(
            db_path,
            ready=False,
            blocked_on=blocked,
            last_backup=backup_str,
            message=(
                f"{len(blocked)} migration(s) need confirmation before the "
                f"app can finish upgrading. See the upgrade page for details."
            ),
            last_applied=to_apply,
            data_changing=data_changing,
        )
        return

    logger.info("startup_migrations_applied", applied=to_apply, backup=backup_str)
    if data_changing:
        record_successful_upgrade(
            db_path, applied=to_apply, last_backup=backup_str, data_changing=True
        )
    else:
        clear_upgrade_state(db_path)


__all__ = ["run_startup_migrations"]

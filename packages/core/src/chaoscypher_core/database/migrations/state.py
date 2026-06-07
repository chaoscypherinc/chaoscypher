# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Upgrade state table.

Sibling of Alembic's ``alembic_version``. Tracks whether the worker
should boot or stay in maintenance mode. Single-row table on purpose —
the state is global to the database, not per-migration.

Consumed by:

- The tier-aware startup runner (writes ``ready=False`` + blocked_on
  when it finds pending NEEDS_CONFIRMATION migrations).
- The Cortex upgrade-gate middleware (returns 503 on /api/* unless ready).
- The Neuron worker's upgrade gate (sleep-loops while ready=False).
- The Cortex ``/upgrade`` API and Interface maintenance page.
- The CLI ``chaoscypher db migrate`` subcommand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text

from chaoscypher_core.adapters.sqlite.engine import get_engine


if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy import Connection


logger = structlog.get_logger(__name__)


_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS chaoscypher_upgrade_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ready INTEGER NOT NULL DEFAULT 1,
    blocked_on TEXT NOT NULL DEFAULT '[]',
    last_backup TEXT,
    message TEXT NOT NULL DEFAULT '',
    last_applied TEXT NOT NULL DEFAULT '[]',
    data_changing INTEGER NOT NULL DEFAULT 0
)
"""


@dataclass(frozen=True)
class UpgradeState:
    """Typed projection of the chaoscypher_upgrade_state row."""

    ready: bool
    blocked_on: list[str] = field(default_factory=list)
    last_backup: str | None = None
    message: str = ""
    last_applied: list[str] = field(default_factory=list)
    data_changing: bool = False


def _ensure_columns(conn: Connection) -> None:
    """Add new columns to a pre-existing upgrade-state table (idempotent)."""
    existing = {
        row[1]
        for row in conn.exec_driver_sql(
            "PRAGMA table_info(chaoscypher_upgrade_state)"
        ).fetchall()
    }
    if "last_applied" not in existing:
        conn.exec_driver_sql(
            "ALTER TABLE chaoscypher_upgrade_state "
            "ADD COLUMN last_applied TEXT NOT NULL DEFAULT '[]'"
        )
    if "data_changing" not in existing:
        conn.exec_driver_sql(
            "ALTER TABLE chaoscypher_upgrade_state "
            "ADD COLUMN data_changing INTEGER NOT NULL DEFAULT 0"
        )


def _ensure_table(db_path: Path) -> None:
    engine = get_engine(db_path)
    with engine.begin() as conn:
        conn.exec_driver_sql(_TABLE_DDL)
        conn.exec_driver_sql(
            "INSERT OR IGNORE INTO chaoscypher_upgrade_state (id, ready) VALUES (1, 1)"
        )
        _ensure_columns(conn)


def get_upgrade_state(db_path: Path) -> UpgradeState:
    """Read the current upgrade state row."""
    _ensure_table(db_path)
    engine = get_engine(db_path)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT ready, blocked_on, last_backup, message, "
                "       last_applied, data_changing "
                "FROM chaoscypher_upgrade_state WHERE id = 1"
            )
        ).fetchone()
    if row is None:
        return UpgradeState(ready=True)
    return UpgradeState(
        ready=bool(row[0]),
        blocked_on=json.loads(row[1] or "[]"),
        last_backup=row[2],
        message=row[3] or "",
        last_applied=json.loads(row[4] or "[]"),
        data_changing=bool(row[5]),
    )


def set_upgrade_state(
    db_path: Path,
    *,
    ready: bool,
    blocked_on: list[str],
    last_backup: str | None,
    message: str,
    last_applied: list[str] | None = None,
    data_changing: bool = False,
) -> None:
    """Upsert the upgrade-state row."""
    _ensure_table(db_path)
    engine = get_engine(db_path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE chaoscypher_upgrade_state "
                "SET ready = :ready, blocked_on = :blocked_on, "
                "    last_backup = :last_backup, message = :message, "
                "    last_applied = :last_applied, data_changing = :data_changing "
                "WHERE id = 1"
            ),
            {
                "ready": 1 if ready else 0,
                "blocked_on": json.dumps(blocked_on),
                "last_backup": last_backup,
                "message": message,
                "last_applied": json.dumps(last_applied or []),
                "data_changing": 1 if data_changing else 0,
            },
        )
    logger.info(
        "upgrade_state_updated",
        ready=ready,
        blocked_on=blocked_on,
        last_backup=last_backup,
        data_changing=data_changing,
    )


# Substrings that mark a SQLite apply failure caused by the live schema
# being AHEAD of its recorded stamp — a migration re-creates an object that
# already exists. The usual cause is an upgrade interrupted partway (the
# DDL ran but the stamp was never written), or a legacy DB built by the
# retired reflective auto-migrator.
SCHEMA_AHEAD_OF_STAMP_SIGNATURES: tuple[str, ...] = (
    "duplicate column",
    "already exists",
    "no such column",
)


def describe_apply_failure(exc: Exception, *, last_backup: str | None) -> str:
    """Turn a migration apply exception into an honest, actionable message.

    Plain-language summary first (so the gist is skimmable), then the
    underlying error and the safe next step. Detects the "schema ahead of
    its recorded version" signature so the message names the real cause
    instead of a generic failure. Shared by the startup runner and the MCP
    apply handler so every surface speaks with one voice.
    """
    err = " ".join(str(exc).split())
    is_drift = any(sig in err.lower() for sig in SCHEMA_AHEAD_OF_STAMP_SIGNATURES)

    if last_backup:
        recovery = (
            "Your data is safe — a pre-upgrade backup was saved. Roll back from the "
            "upgrade page (or run `chaoscypher db migrate rollback`), then retry."
        )
    else:
        recovery = (
            "Check the server logs (stderr) and roll back from a backup, if one "
            "exists, before retrying."
        )

    if is_drift:
        return (
            "The database could not finish upgrading because a migration tried to "
            "create schema that already exists. The database's recorded version is "
            "behind its actual schema — usually the result of an upgrade that was "
            f"interrupted partway. {recovery} If it recurs, the database may need to "
            f"be re-created. (Underlying error: {err})"
        )
    return (
        f"The database upgrade failed before completing. {recovery} "
        f"(Underlying error: {err})"
    )


def clear_upgrade_state(db_path: Path) -> None:
    """Mark the DB ready, clear blocked_on and message."""
    set_upgrade_state(
        db_path, ready=True, blocked_on=[], last_backup=None, message=""
    )


def record_successful_upgrade(
    db_path: Path,
    *,
    applied: list[str],
    last_backup: str | None,
    data_changing: bool,
) -> None:
    """Mark ready after a successful auto-apply, retaining rollback info.

    Unlike :func:`clear_upgrade_state`, this keeps ``last_backup`` and the
    applied revision list so a silently-applied (data-changing) upgrade can
    still be rolled back and surfaced to the operator afterwards.
    """
    set_upgrade_state(
        db_path,
        ready=True,
        blocked_on=[],
        last_backup=last_backup,
        message="",
        last_applied=applied,
        data_changing=data_changing,
    )


__all__ = [
    "SCHEMA_AHEAD_OF_STAMP_SIGNATURES",
    "UpgradeState",
    "clear_upgrade_state",
    "describe_apply_failure",
    "get_upgrade_state",
    "record_successful_upgrade",
    "set_upgrade_state",
]

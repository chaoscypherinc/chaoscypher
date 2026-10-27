# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The ``memory_audit`` table: one row per ``forget``, never the text (migration 0020)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlmodel import col, select

from chaoscypher_core.adapters.sqlite.models import MemoryAuditRow
from chaoscypher_core.exceptions import InvalidStateError
from chaoscypher_core.utils.id import generate_hex_id


if TYPE_CHECKING:
    from sqlmodel import Session

FORGET = "forget"


def count_forgotten(session: Session, database_name: str, since: str) -> int:
    """How many memories were forgotten in ``database_name`` on or after ``since`` (ISO date)."""
    statement = select(func.count()).where(
        MemoryAuditRow.database_name == database_name,
        MemoryAuditRow.action == FORGET,
        col(MemoryAuditRow.at) >= since,
    )
    return int(session.exec(statement).one())


def record(
    session: Session,
    database_name: str,
    *,
    action: str,
    handle: str,
    by: str | None,
    scope: str | None,
    facts_removed: int,
    facts_kept: int,
) -> MemoryAuditRow:
    """Add one audit row (never the text) to the session's transaction; its owner commits it.

    A forget writes it inside the delete's own transaction, so a memory is never
    gone without its row.

    Raises:
        InvalidStateError: The session is in no ``adapter.transaction()``: the row would
            sit uncommitted until some later commit, or go with the next rollback.
    """
    if getattr(session, "_transaction_depth", 0) == 0:
        msg = "A memory's audit row is written inside the transaction that forgets it."
        raise InvalidStateError(msg)
    row = MemoryAuditRow(
        id=generate_hex_id(),
        database_name=database_name,
        at=datetime.now(UTC).isoformat(),
        action=action,
        handle=handle,
        by=by,
        scope=scope,
        facts_removed=facts_removed,
        facts_kept=facts_kept,
    )
    session.add(row)
    return row

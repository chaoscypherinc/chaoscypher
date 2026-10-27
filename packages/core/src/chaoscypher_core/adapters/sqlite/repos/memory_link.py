# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A memory's link state on its source row (migration 0024): claims, heartbeats and outcomes.

The link step (``mcp/memory_link.py``) claims a memory with a compare-and-set on
``memory_link_claim``, refreshes ``last_activity_at`` while it holds the claim, and
records the outcome. Every mark names the claim token in its WHERE, so a False
return means the row is gone or another process took it over.

Every function takes the SQLAlchemy engine and opens its own short session: it is
safe to call from a worker thread (the link step runs these off the event loop), and
it never reads the storage adapter's long-lived session, whose identity map would
hide a state another call has just written. This is the engine-based standalone
repository carve-out of CC011.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, and_, case, or_, update
from sqlalchemy.orm import load_only
from sqlmodel import Session, col, select

from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.models import (
    MEMORY_SOURCE_TYPE,
    MemoryLinkState,
    SourceErrorStage,
    is_memory_source,
)


if TYPE_CHECKING:
    from sqlalchemy import Engine

_LINKED = (MemoryLinkState.LINKED.value, MemoryLinkState.LINKED_EMPTY.value)


@dataclass(frozen=True)
class LinkRow:
    """The columns of a memory row the link step and the status copy read."""

    id: str
    state: MemoryLinkState | None
    claim: str | None
    last_activity_at: datetime | None  # aware UTC
    recovery_attempts: int
    commit_complete: bool
    indexing_complete: bool
    full_text: str | None
    memory_by: str | None
    memory_scope: str | None
    created_at: datetime | None


def _aware(value: datetime | None) -> datetime | None:
    """SQLite stores datetimes without a zone; every writer writes UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _link_row(row: SourceRow, *, with_text: bool) -> LinkRow:
    """The link columns of ``row`` (its text only ``with_text``), datetimes aware UTC."""
    state = row.memory_link_state
    return LinkRow(
        id=row.id,
        state=MemoryLinkState(state) if state is not None else None,
        claim=row.memory_link_claim,
        last_activity_at=_aware(row.last_activity_at),
        recovery_attempts=row.recovery_attempts,
        commit_complete=row.commit_complete,
        indexing_complete=row.indexing_complete,
        full_text=row.full_text if with_text else None,
        memory_by=row.memory_by,
        memory_scope=row.memory_scope,
        created_at=_aware(row.created_at),
    )


def _held(source_id: str, token: str) -> ColumnElement[bool]:
    """The WHERE of a write by the claim's holder: this row, still claimed by ``token``."""
    return and_(col(SourceRow.id) == source_id, col(SourceRow.memory_link_claim) == token)


_LINK_COLUMNS = (
    SourceRow.id,
    SourceRow.memory_link_state,
    SourceRow.memory_link_claim,
    SourceRow.last_activity_at,
    SourceRow.recovery_attempts,
    SourceRow.commit_complete,
    SourceRow.indexing_complete,
    SourceRow.memory_by,
    SourceRow.memory_scope,
    SourceRow.created_at,
)


class MemoryLinkRepository:
    """The link-state reads and writes, each in its own short ``Session(self._engine)``."""

    def __init__(self, engine: Engine) -> None:
        """Bind to the database file's engine (``get_engine(db_path)``)."""
        self._engine = engine

    def _update(self, where: ColumnElement[bool], values: dict[str, Any]) -> bool:
        """Run one UPDATE in its own transaction; True when exactly one row changed."""
        with Session(self._engine) as session:
            result = session.execute(update(SourceRow).where(where).values(**values))
            session.commit()
            return int(result.rowcount or 0) == 1  # type: ignore[attr-defined]  # CursorResult at runtime

    def claim(
        self,
        source_id: str,
        token: str,
        *,
        now: datetime,
        stale_before: datetime,
        include_empty: bool = False,
    ) -> bool:
        """Claim a memory for linking: True when this token now holds it.

        Claimable is read from the state alone, as the views read it: a memory is
        claimable unless it is ``linked`` or ``linked_empty`` (with ``include_empty``,
        ``linked_empty`` is claimable too), and only when nobody holds it, this token
        already does, or the holder's heartbeat is older than ``stale_before``. A
        ``linked_empty`` memory claimed with ``include_empty`` is reopened in the same
        statement (``commit_complete`` 0, ``memory_linked_at`` NULL), so a claim that is
        released or fails later leaves an ordinary unlinked memory behind.
        """
        state = col(SourceRow.memory_link_state)
        empty = MemoryLinkState.LINKED_EMPTY.value
        unlinked: ColumnElement[bool] = or_(state.is_(None), state.notin_(_LINKED))
        if include_empty:
            unlinked = or_(unlinked, state == empty)
        free = or_(
            col(SourceRow.memory_link_claim).is_(None),
            col(SourceRow.memory_link_claim) == token,
            col(SourceRow.last_activity_at) < stale_before,
        )
        where = and_(
            col(SourceRow.id) == source_id,
            col(SourceRow.source_type) == MEMORY_SOURCE_TYPE,
            unlinked,
            free,
        )
        values: dict[str, Any] = {
            "memory_link_state": MemoryLinkState.LINKING.value,
            "memory_link_claim": token,
            "last_activity_at": now,
        }
        if include_empty:
            values["commit_complete"] = case(
                (state == empty, False), else_=col(SourceRow.commit_complete)
            )
            values["memory_linked_at"] = case(
                (state == empty, None), else_=col(SourceRow.memory_linked_at)
            )
        return self._update(where, values)

    def touch(self, source_id: str, token: str, *, now: datetime) -> bool:
        """Refresh the claim's heartbeat."""
        return self._update(_held(source_id, token), {"last_activity_at": now})

    def release(self, source_id: str, token: str) -> bool:
        """Give the claim up without an outcome: back to pending (shutdown, cancellation)."""
        values = {"memory_link_state": MemoryLinkState.PENDING.value, "memory_link_claim": None}
        return self._update(_held(source_id, token), values)

    def mark_no_model(self, source_id: str, token: str) -> bool:
        """No extraction model is configured; not an attempt, so the count stays."""
        values = {"memory_link_state": MemoryLinkState.NO_MODEL.value, "memory_link_claim": None}
        return self._update(_held(source_id, token), values)

    def mark_failed(
        self,
        source_id: str,
        token: str,
        *,
        stage: SourceErrorStage,
        reason: str,
        now: datetime,
    ) -> bool:
        """The link raised at ``stage``: count the attempt and keep a fixed reason (never the text)."""
        values = {
            "memory_link_state": MemoryLinkState.FAILED.value,
            "memory_link_claim": None,
            "recovery_attempts": col(SourceRow.recovery_attempts) + 1,
            "error_stage": stage.value,
            "error_message": reason,
            "last_activity_at": now,
        }
        return self._update(_held(source_id, token), values)

    def mark_linked(self, source_id: str, token: str, *, empty: bool, now: datetime) -> bool:
        """The link finished: ``linked`` (or ``linked_empty`` when it attests no node)."""
        state = MemoryLinkState.LINKED_EMPTY if empty else MemoryLinkState.LINKED
        values = {
            "memory_link_state": state.value,
            "memory_linked_at": now,
            "memory_link_claim": None,
            "error_stage": None,
            "error_message": None,
        }
        return self._update(_held(source_id, token), values)

    def read_link_row(self, source_id: str) -> LinkRow | None:
        """One memory's link columns and its text, or None when the row is gone."""
        with Session(self._engine) as session:
            row = session.get(SourceRow, source_id)
            if row is None or not is_memory_source(row):
                return None
            return _link_row(row, with_text=True)

    def link_rows(
        self,
        database_name: str,
        *,
        source_ids: Collection[str] | None = None,
        scope: str | None = None,
    ) -> list[LinkRow]:
        """The memory rows of ``database_name``, oldest first, without their text.

        ``source_ids`` narrows to those rows (a page of a listing); ``scope`` to one
        scope's memories (None: every memory, as ``memory_rows`` reads it), read in SQL
        so a large scope never becomes an id list past SQLite's variable limit.
        """
        statement = (
            select(SourceRow)
            .options(load_only(*_LINK_COLUMNS))  # type: ignore[arg-type]  # SQLModel column attrs
            .where(col(SourceRow.database_name) == database_name)
            .where(col(SourceRow.source_type) == MEMORY_SOURCE_TYPE)
            .order_by(col(SourceRow.created_at), col(SourceRow.id))
        )
        if source_ids is not None:
            statement = statement.where(col(SourceRow.id).in_(list(source_ids)))
        if scope is not None:
            statement = statement.where(col(SourceRow.memory_scope) == scope)
        with Session(self._engine) as session:
            return [_link_row(row, with_text=False) for row in session.exec(statement)]

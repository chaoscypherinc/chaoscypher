# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``SafeSession.discard_failed_work``: what a catch calls so a failed call leaves the session usable.

A long-lived session (an Engine's, which every tool call of ``chaoscypher mcp`` shares) whose
flush failed stayed in ``PendingRollbackError`` until someone called ``rollback()``. Every
catch-and-continue around it (``tool_handler``, the executor) went on with the session
broken, and every later call in the process failed. The catch now discards the failed work;
inside a ``transaction()`` block the block's owner does.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlmodel import select

from chaoscypher_core.adapters.sqlite import SqliteAdapter
from chaoscypher_core.adapters.sqlite.models import GraphEdge, GraphTemplate
from chaoscypher_core.database.migrations.runner import upgrade_to_head


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession


@pytest.fixture
def adapter(tmp_path: Path) -> SqliteAdapter:
    db = tmp_path / "app.db"
    upgrade_to_head(db)
    adapter = SqliteAdapter(db_path=str(db), database_name="notes")
    adapter.connect()
    yield adapter
    adapter.disconnect()


def _edge_to_nowhere() -> GraphEdge:
    """An edge whose template and nodes do not exist: its insert fails a foreign key."""
    return GraphEdge(
        id="edge_to_nowhere",
        database_name="notes",
        graph_name="knowledge",
        template_id="no_such_template",
        source_node_id="no_such_node",
        target_node_id="no_such_node",
        label="about",
        properties={},
    )


def _still_reads(session: SafeSession) -> None:
    assert session.exec(select(GraphTemplate.id)).all() == []


def test_after_a_failed_flush_discarding_leaves_the_session_usable(
    adapter: SqliteAdapter,
) -> None:
    session = adapter.session
    assert session is not None
    session.add(_edge_to_nowhere())
    with pytest.raises(IntegrityError):
        session.flush()
    session.discard_failed_work()
    _still_reads(session)


def test_after_a_failed_autoflush_discarding_leaves_the_session_usable(
    adapter: SqliteAdapter,
) -> None:
    session = adapter.session
    assert session is not None
    session.add(_edge_to_nowhere())
    with pytest.raises(IntegrityError):
        session.exec(select(GraphEdge.id)).all()  # the query flushes the pending edge first
    session.discard_failed_work()
    _still_reads(session)


def _waits_for_its_rollback(session: SafeSession) -> bool:
    try:
        session.exec(select(GraphTemplate.id)).all()
    except PendingRollbackError:
        return True
    return False


def test_inside_a_transaction_block_discarding_leaves_it_to_the_block(
    adapter: SqliteAdapter,
) -> None:
    """The block owns its transaction: discarding inside it does nothing, and the block rolls back."""
    session = adapter.session
    assert session is not None
    inside: list[bool] = []

    def failing_block() -> None:
        with adapter.transaction():
            session.add(_edge_to_nowhere())
            try:
                session.flush()
            except IntegrityError:
                session.discard_failed_work()
                inside.append(_waits_for_its_rollback(session))  # still the block's to end
                raise

    with pytest.raises(IntegrityError):
        failing_block()
    assert inside == [True]
    _still_reads(session)

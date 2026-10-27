# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A database busy past the wait answers a fixed line, never SQL (spec 2026-10-24 § 3, G2).

Another connection holds the writer lock while ``forget`` runs: the call fails with
``isError`` and the fixed busy line (SQLAlchemy's own text carries the SQL and its bound
parameters, which can quote a memory), and the memory is still whole afterwards. The
world's engine waits 1 s instead of 60, and the delete tries once instead of retrying.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.exc import OperationalError

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.mcp import memory
from chaoscypher_core.mcp.memory import NOT_STORED
from chaoscypher_core.services.graph.management import release
from chaoscypher_core.utils.retry import DbLockRetryPolicy
from chaoscypher_core.utils.tool_failure import TOOL_FAILURES
from tests.unit.mcp.memory_processes import KYIV, LYON, ROME, WRITERS_SCRIPT, db_path
from tests.unit.mcp.memory_world import build_world, call, call_raw


if TYPE_CHECKING:
    from chaoscypher_core.models import Edge


@pytest.fixture
async def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SimpleNamespace]:
    get_engine(db_path(tmp_path), busy_timeout_ms=1000, connection_timeout=1)  # cached first

    def once(_self: Any, fn: Any, *args: Any, operation_name: str | None = None, **kw: Any) -> Any:
        return fn(*args, **kw)

    monkeypatch.setattr(DbLockRetryPolicy, "run_sync", once)
    w = build_world(tmp_path, monkeypatch, script=WRITERS_SCRIPT)
    try:
        yield w
    finally:
        await w.linker.close()
        w.engine.close()


async def test_a_forget_blocked_past_the_busy_timeout_answers_the_busy_line(
    world: SimpleNamespace, tmp_path: Path
) -> None:
    await call(world.server, "remember", LYON)
    preview = await call(world.server, "forget", {"query": "Lyon"})
    found = re.search(r'forget\(handle="([^"]+)"', preview)
    assert found, preview
    handle = found.group(1)
    holder = sqlite3.connect(db_path(tmp_path), timeout=0.1)
    try:
        holder.execute("BEGIN IMMEDIATE")
        text, is_error = await call_raw(world.server, "forget", {"handle": handle, "confirm": True})
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert (text, is_error) == (TOOL_FAILURES["DATABASE_BUSY"], True)
    assert "moved to Lyon" in await call(world.server, "list_memories", {})  # still whole
    assert "Forgotten" in await call(world.server, "forget", {"handle": handle, "confirm": True})


async def test_a_remember_blocked_past_the_busy_timeout_answers_not_stored(
    world: SimpleNamespace, tmp_path: Path
) -> None:
    """The store step's own answer, and the server writes again once the lock is free."""
    await call(world.server, "list_memories", {})  # the schema exists before the lock
    holder = sqlite3.connect(db_path(tmp_path), timeout=0.1)
    try:
        holder.execute("BEGIN IMMEDIATE")
        text, is_error = await call_raw(world.server, "remember", LYON)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert (text, is_error) == (NOT_STORED, True)
    assert (await call(world.server, "remember", LYON)).startswith("Remembered")


async def _forget_preview_of_rome(world: SimpleNamespace) -> str:
    """Lyon, Rome, Kyiv remembered (each closing the one before); Rome's handle."""
    for args in (LYON, ROME, KYIV):
        await call(world.server, "remember", args)
    preview = await call(world.server, "forget", {"query": "Rome"})
    found = re.search(r'forget\(handle="([^"]+)"', preview)
    assert found, preview
    return found.group(1)


def _audits(tmp_path: Path) -> int:
    with sqlite3.connect(db_path(tmp_path)) as db:
        [(audits,)] = db.execute("SELECT count(*) FROM memory_audit WHERE action = 'forget'")
    return int(audits)


def _located_in(world: SimpleNamespace) -> dict[str, Edge]:
    """Pierre's located_in facts by city, read fresh."""
    world.engine.expire_cached_rows()
    graph = world.graph
    [pierre] = [
        n
        for n in graph.list_nodes(limit=1000, include_embedding=False)
        if n.label == "Pierre Bezukhov"
    ]
    facts = {}
    for edge in graph.edges_from(pierre.id, "located_in"):
        target = graph.get_node(edge.target_node_id, include_embedding=False)
        assert target is not None
        facts[target.label] = edge
    return facts


def _memory_id(tmp_path: Path, text: str) -> str:
    with sqlite3.connect(db_path(tmp_path)) as db:
        [(source_id,)] = db.execute("SELECT id FROM sources WHERE full_text = ?", (text,))
    return str(source_id)


async def test_a_forget_whose_delete_committed_answers_forgotten_while_the_lock_is_taken(
    world: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the delete commits the memory is gone: the answer says so, and the audit row is kept.

    Another process takes the writer lock the moment the delete commits and holds it past
    the wait. What forget settles must not turn into "Try again" (a retry would say the
    handle is unknown) nor lose the forget's audit row. Forgetting Rome, between Lyon and
    Kyiv, leaves Lyon for the recompute alone to settle: closed by Kyiv, not reopened.
    """
    handle = await _forget_preview_of_rome(world)
    holder = sqlite3.connect(db_path(tmp_path), timeout=0.1)
    real_refresh = release.refresh_after_release

    async def lock_taken_after_the_delete(*args: Any, **kwargs: Any) -> None:
        holder.execute("BEGIN IMMEDIATE")
        await real_refresh(*args, **kwargs)

    monkeypatch.setattr(release, "refresh_after_release", lock_taken_after_the_delete)
    try:
        text, is_error = await call_raw(world.server, "forget", {"handle": handle, "confirm": True})
    finally:
        if holder.in_transaction:
            holder.execute("ROLLBACK")
        holder.close()
    assert not is_error, text
    assert text.startswith(f"Forgotten {handle}:")
    assert _audits(tmp_path) == 1
    assert "Rome" not in await call(world.server, "list_memories", {})
    facts = _located_in(world)
    assert sorted(facts) == ["Kyiv", "Lyon"]
    assert facts["Kyiv"].valid_to is None
    assert (facts["Lyon"].valid_to, facts["Lyon"].closed_by_source_id) == (
        date(2026, 10, 21),
        _memory_id(tmp_path, KYIV["text"]),
    )


async def test_a_forget_whose_settle_meets_a_busy_database_leaves_the_memory_whole(
    world: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The settle and the audit row commit with the delete: one busy, nothing of it lands."""
    handle = await _forget_preview_of_rome(world)
    real_recompute = memory.recompute_single_valued

    def busy(*_args: Any, **_kwargs: Any) -> Any:
        raise OperationalError(
            "UPDATE graph_edges", {}, sqlite3.OperationalError("database is locked")
        )

    monkeypatch.setattr(memory, "recompute_single_valued", busy)
    text, is_error = await call_raw(world.server, "forget", {"handle": handle, "confirm": True})
    assert (text, is_error) == (TOOL_FAILURES["DATABASE_BUSY"], True)
    assert "moved to Rome" in await call(world.server, "list_memories", {})
    assert _audits(tmp_path) == 0
    facts = _located_in(world)
    assert (facts["Lyon"].valid_to, facts["Lyon"].closed_by_source_id) == (
        date(2026, 10, 20),
        _memory_id(tmp_path, ROME["text"]),
    )
    monkeypatch.setattr(memory, "recompute_single_valued", real_recompute)
    assert "Forgotten" in await call(world.server, "forget", {"handle": handle, "confirm": True})
    assert _audits(tmp_path) == 1
